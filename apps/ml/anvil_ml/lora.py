"""Tiny LoRA implementation for SAM2 mask-decoder fine-tuning.

We freeze the image encoder and prompt encoder, then inject low-rank adapters
on every nn.Linear inside the mask decoder's attention layers
(q_proj, k_proj, v_proj, out_proj). Only the LoRA tensors carry gradients.

State is saved as a flat dict of LoRA A/B tensors keyed by their dotted
module path within the mask decoder (e.g.
"transformer.layers.0.self_attn.q_proj"), which keeps adapters portable
across model rebuilds.
"""
from __future__ import annotations

import math
from pathlib import Path

import torch
import torch.nn as nn

LORA_TARGETS = ("q_proj", "k_proj", "v_proj", "out_proj")


class LoRALinear(nn.Module):
    """Wrap an nn.Linear with a frozen base + trainable low-rank update."""

    def __init__(self, base: nn.Linear, r: int = 8, alpha: int = 16) -> None:
        super().__init__()
        self.base = base
        for p in self.base.parameters():
            p.requires_grad = False
        in_f, out_f = base.in_features, base.out_features
        self.lora_A = nn.Linear(in_f, r, bias=False)
        self.lora_B = nn.Linear(r, out_f, bias=False)
        nn.init.kaiming_uniform_(self.lora_A.weight, a=math.sqrt(5))
        nn.init.zeros_(self.lora_B.weight)
        self.scale = alpha / r
        self.r = r
        self.alpha = alpha

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.base(x) + self.lora_B(self.lora_A(x)) * self.scale


def inject_lora(mask_decoder: nn.Module, r: int = 8, alpha: int = 16) -> list[str]:
    """Replace every targeted nn.Linear in `mask_decoder` with a LoRALinear.

    Returns the list of dotted paths that were wrapped.
    """
    wrapped: list[str] = []
    for mod_name, mod in mask_decoder.named_modules():
        for proj in LORA_TARGETS:
            base = getattr(mod, proj, None)
            if isinstance(base, nn.Linear) and not isinstance(base, LoRALinear):
                setattr(mod, proj, LoRALinear(base, r=r, alpha=alpha).to(base.weight.device))
                wrapped.append(f"{mod_name}.{proj}" if mod_name else proj)
    return wrapped


def lora_parameters(mask_decoder: nn.Module):
    """Yield only the trainable LoRA tensors (A and B weights)."""
    for mod in mask_decoder.modules():
        if isinstance(mod, LoRALinear):
            yield mod.lora_A.weight
            yield mod.lora_B.weight


def freeze_all_but_lora(model: nn.Module) -> None:
    for p in model.parameters():
        p.requires_grad = False
    for mod in model.modules():
        if isinstance(mod, LoRALinear):
            mod.lora_A.weight.requires_grad = True
            mod.lora_B.weight.requires_grad = True


def lora_state_dict(mask_decoder: nn.Module) -> dict[str, torch.Tensor]:
    """Return only LoRA tensors, keyed by their path within mask_decoder."""
    out: dict[str, torch.Tensor] = {}
    for name, mod in mask_decoder.named_modules():
        if isinstance(mod, LoRALinear):
            out[f"{name}.lora_A"] = mod.lora_A.weight.detach().cpu()
            out[f"{name}.lora_B"] = mod.lora_B.weight.detach().cpu()
            out[f"{name}.meta"] = torch.tensor([mod.r, mod.alpha], dtype=torch.int32)
    return out


def save_lora(mask_decoder: nn.Module, path: str | Path) -> str:
    sd = lora_state_dict(mask_decoder)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    torch.save(sd, p)
    return str(p)


def load_lora(mask_decoder: nn.Module, path: str | Path) -> int:
    """Inject LoRA matching the saved adapter then load weights. Returns count loaded."""
    sd = torch.load(str(path), map_location="cpu", weights_only=True)
    # Determine r/alpha from any meta tensor (they should all match)
    meta_keys = [k for k in sd if k.endswith(".meta")]
    if not meta_keys:
        raise ValueError(f"adapter at {path} has no meta tensor; corrupt file")
    r, alpha = sd[meta_keys[0]].tolist()
    inject_lora(mask_decoder, r=int(r), alpha=int(alpha))
    loaded = 0
    for name, mod in mask_decoder.named_modules():
        if isinstance(mod, LoRALinear):
            ka, kb = f"{name}.lora_A", f"{name}.lora_B"
            if ka in sd and kb in sd:
                mod.lora_A.weight.data.copy_(sd[ka].to(mod.lora_A.weight.device))
                mod.lora_B.weight.data.copy_(sd[kb].to(mod.lora_B.weight.device))
                loaded += 1
    return loaded
