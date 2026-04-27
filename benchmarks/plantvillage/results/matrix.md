# PlantVillage SAM2 fine-tune benchmark — aggregated results

Total runs: **4**.

| device | variant | config | pre IoU | post IoU |   Δ   |  wall  | peak mem | trainable | cost |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| mps | base_plus | vanilla | 0.7832 | 0.7832 | +0.0000 | 0s | 0.00 GB | 0 | $0.00 |
| mps | base_plus | lora_r8 | 0.7832 | 0.9347 | +0.1515 | 473s | 3.32 GB | 94,208 | $0.00 |
| mps | base_plus | lora_r16 | 0.7832 | 0.9373 | +0.1541 | 471s | 3.32 GB | 188,416 | $0.00 |
| mps | base_plus | full_decoder | 0.7832 | 0.9432 | +0.1600 | 497s | 3.35 GB | 4,215,109 | $0.00 |

## Per-config best result (across devices)

| config | best post IoU | device | wall | trainable |
|---|---:|---|---:|---:|
| vanilla | 0.7832 | mps | 0s | 0 |
| lora_r8 | 0.9347 | mps | 473s | 94,208 |
| lora_r16 | 0.9373 | mps | 471s | 188,416 |
| full_decoder | 0.9432 | mps | 497s | 4,215,109 |
