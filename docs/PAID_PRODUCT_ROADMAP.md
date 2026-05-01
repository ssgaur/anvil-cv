# Anvil Paid Product Roadmap

This is the canonical roadmap for turning Anvil from an OSS local annotation tool
into a paid team product. It captures the current design direction for review
management, fine-tuning, feature flags, packaging, and development order.

## Product positioning

We should position Anvil as:

| Edition | Target | Core promise |
| --- | --- | --- |
| **OSS / Individual** | Solo annotator, researcher, local experiments | Fast local SAM2 annotation + export + simple review |
| **Paid / Team** | Annotation teams, QA, dataset ops, ML teams | Workflow orchestration, review management, team permissions, model fine-tuning lifecycle |

## 1. Feature flag / edition strategy

We should not fork the codebase. Keep one product, gate advanced capabilities.

Local-first feature flags:

```txt
ANVIL_EDITION=oss | pro
ANVIL_FEATURE_TEAMS=true
ANVIL_FEATURE_REVIEW_WORKFLOWS=true
ANVIL_FEATURE_ASSIGNMENTS=true
ANVIL_FEATURE_CONSENSUS=true
ANVIL_FEATURE_AI_REVIEW=true
ANVIL_FEATURE_FINE_TUNING=true
ANVIL_FEATURE_MODEL_REGISTRY=true
ANVIL_FEATURE_AUDIT_LOG=true
ANVIL_FEATURE_WEBHOOKS=true
ANVIL_FEATURE_ACTIVE_LEARNING=true
```

Also store runtime flags in `InstanceState`, so local dev can toggle without
deployment changes.

**OSS default:** keep current simple behavior: global roles,
project/dataset/classes/workview, simple submit/approve/reject, comments,
export, basic fine-tune if we choose.

**Paid default:** unlock teams, project memberships, workflow builder, advanced
review queues, model studio, audit logs, webhooks, and model deployment
controls.

## Feature 1: Full review management workflow

The V7 screenshot gives the right mental model: a **workflow canvas** where
dataset items flow through stages like Sampling -> Annotate -> Review -> 2nd
Review -> Complete, with accepted/rejected routing and assignees per stage.

We should build this as a real workflow engine, not just more fields on `Image`.

## Current foundation we already have

Anvil already has:

- Global roles: `admin`, `reviewer`, `labeler`, `viewer`
- Image lifecycle: `new`, `in_progress`, `submitted`, `approved`, `rejected`
- Reject reason and comments
- Workview submit/approve/reject/reopen
- Dataset filters by review status
- Bulk review actions
- Comments/pins
- COCO export filtered by review status

This is good for OSS/simple teams, but paid review needs a bigger abstraction:
**WorkItem + Workflow + Stage + Assignment**.

## Paid team role design

Current global `User.role` should remain as OSS fallback. Paid should add
project/org memberships with multiple roles.

### New role model

A user can have multiple roles at org/project level:

| Role | Purpose |
| --- | --- |
| **Owner** | Billing/org ownership, all permissions |
| **Admin** | Manage users, projects, workflows |
| **Project Manager** | Configure dataset workflow, assignments, deadlines |
| **Labeler** | Annotate assigned work |
| **Reviewer** | Review submitted work |
| **QA Lead** | Override decisions, second-level review, audit |
| **ML Engineer** | Train/evaluate/deploy fine-tuned models |
| **Viewer** | Read-only |

Instead of `role = reviewer`, paid should use permissions:

```txt
project.workflow.configure
project.item.assign
project.annotation.create
project.annotation.review
project.annotation.override
project.model.train
project.model.deploy
project.audit.view
```

This lets one person be both `Labeler + Reviewer + ML Engineer`, but workflow
policy can still prevent self-review.

## Workflow concepts

### Core entities

| Entity | Meaning |
| --- | --- |
| `Workflow` | Named pipeline for a project/dataset |
| `WorkflowStage` | Node on workflow canvas |
| `WorkflowEdge` | Connection/routing between stages |
| `WorkItem` | One image/task moving through workflow |
| `Assignment` | User/team assigned to a work item/stage |
| `ReviewDecision` | Approve/reject/needs changes decision |
| `ReviewRound` | Attempt number for a stage |
| `AuditEvent` | Immutable event log |
| `Team` | Group of users used in assignment pools |

### Stage types inspired by V7

| Stage | What it does |
| --- | --- |
| **Dataset/Input** | Entry point from dataset/filter |
| **Sampling** | Route N% or filtered subsets into branches |
| **Annotate** | Labeling stage with assignee pool |
| **Review** | Human review stage |
| **2nd Review / QA** | Optional stricter review |
| **Consensus** | Multiple labelers annotate same image, compare agreement |
| **AI Review** | Auto-check labels using model/rules |
| **Model Assist** | Run active fine-tuned model before human label |
| **Logic** | Branch based on class, uncertainty, status, agreement |
| **Webhook** | Notify external system |
| **Archive** | Retire/skipped items |
| **Complete** | Final approved state, lock/export-ready |

## Workflow state machine

Instead of only `Image.review_status`, paid should track:

```txt
WorkItem.status:
queued
assigned
in_progress
submitted
reviewing
accepted
rejected
rework
completed
archived
blocked
```

Current `Image.review_status` can remain as a simple projection for OSS/UI
compatibility.

### Example paid flow

```txt
Dataset
  -> Sampling 30/70
  -> Annotator-1 / Annotator-2 / Annotator-3
  -> Reviewer-1 / Reviewer-2 / Reviewer-3
  -> 2nd Reviewer
  -> Complete
```

Rejected work should route back to the correct previous annotation stage with
required comments.

## UI design

### 1. Workflow Builder

A paid **Workflow** tab, visually like the V7 screenshot.

Main layout:

| Area | Purpose |
| --- | --- |
| Left nav | Datasets, Workflows, Classes, Models, Reports, Rules |
| Center canvas | Workflow nodes and edges |
| Right panel | Add stages palette |
| Bottom drawer | Preview items flowing through selected stage |
| Top bar | Save / Apply workflow |

Node examples:

```txt
Annotator-1
Any class
Assigned: Mohammed Bawas
Queue: 3
```

```txt
Reviewer-1
Accepted -> next
Rejected -> annotator
Assigned: Sambasivarao K
Queue: 0
```

### 2. Review Center

A dedicated paid page:

```txt
Review Queue
- Assigned to me
- Waiting review
- Rejected by me
- Needs second review
- Consensus conflicts
- AI flagged
```

Reviewer opens item and sees:

- image + annotations
- diff since last submission
- comments/issues
- labeler metadata
- class/property completeness
- approve / reject / request changes
- batch approval for low-risk items

### 3. Assignment dashboard

For project managers:

```txt
Labeler    Assigned  In progress  Submitted  Rejected  Approved  Avg time
A          120       12           20         5         83        2m12s
B          90        8            15         2         65        1m58s
```

### 4. Quality dashboard

Paid reports:

- review acceptance rate
- rejection reasons by class
- labeler disagreement
- reviewer load
- time per item
- bottleneck stages
- consensus score
- model-assisted vs manual performance

## Review workflow development phases

### Phase R0 - Feature flag foundation

- Add edition/feature flag service
- Gate paid nav items and API endpoints
- Keep OSS behavior unchanged
- Add permission vocabulary

### Phase R1 - Team/project memberships

- Add org/project membership tables
- Users can have multiple roles
- Project-level permissions replace global-only RBAC in paid
- Admin UI for team members and roles

### Phase R2 - Work queues and assignments

- Add `WorkItem`, `Assignment`
- Assign images to users/teams
- Add "My queue"
- Add claim/release/reassign
- Preserve existing submit/approve/reject for OSS

### Phase R3 - Workflow engine

- Add workflow/stage/edge schema
- Initial stage types: Dataset, Annotate, Review, Complete
- Route accepted/rejected outcomes
- Stage-level assignee pools
- Stage-level policy: self-review allowed/disallowed, required approvals

### Phase R4 - Workflow builder UI

- Canvas builder inspired by V7
- Stage palette
- Node config side panel
- Save/apply workflow
- Workflow preview with item counts

### Phase R5 - Advanced review

- Multi-stage review
- Second reviewer
- Consensus annotation
- Rejection issue threads
- Audit trail
- Batch review

### Phase R6 - AI review and rules

- Rule checks: missing required class/property, tiny polygons, overlaps,
  impossible labels
- AI review stage using active model
- Flag suspicious labels for human review

## Feature 2: Full fine-tuning for domains where base SAM2 fails

The current model-version system is already a good seed:

- `ModelVersion`
- background training
- LoRA adapter
- active model
- compare base vs fine-tuned
- per-class IoU
- Workview can use active model for prediction

For paid, this should become a proper **Model Studio**.

## Fine-tuning product goal

For cases like fruit defects, base SAM2 may segment the fruit but not subtle
disease/defect boundaries. Paid Anvil should support:

1. Creating a curated training dataset from approved labels
2. Fine-tuning SAM2 adapters
3. Evaluating against base SAM2 and previous versions
4. Reviewing model failures visually
5. Deploying a model into annotation workflow
6. Monitoring model performance and retraining

## Model Studio UX

### Page: Models / Fine-tuning

Sections:

```txt
Model Versions
- Base SAM2
- strawberry-defect-v1
- strawberry-defect-v2 active
```

Each model card:

```txt
Status: Ready
Train images: 1,240
Train annotations: 6,830
Classes: bruise, mold, stem, leaf, rot
Mean IoU: 0.72
Small defect IoU: 0.48
Created by: ML Engineer
Deploy status: Active in Project
```

### Create training run wizard

Steps:

1. **Select data**
   - dataset(s)
   - approved labels only by default
   - include/exclude classes
   - minimum review stage
   - train/validation/test split

2. **Data readiness**
   - number of images
   - number of annotations per class
   - class imbalance warnings
   - unsupported geometry warnings
   - tiny object/defect distribution

3. **Training config**
   - base model
   - epochs
   - LoRA rank/alpha/lr
   - image crop strategy
   - augmentations
   - prompt strategy
   - device/worker

4. **Evaluate**
   - compare vs base SAM2
   - compare vs active model
   - per-class IoU
   - small-object IoU
   - precision/recall proxy
   - visual failure gallery

5. **Deploy**
   - set active model
   - deploy only to workflow stage
   - canary percentage
   - rollback button

## Fine-tuning data model

Add or evolve:

| Entity | Purpose |
| --- | --- |
| `TrainingDatasetSnapshot` | Immutable list of image/annotation IDs used for training |
| `TrainingRun` | One train job with config/status/logs |
| `ModelArtifact` | Adapter/checkpoint metadata |
| `EvaluationRun` | Eval against holdout/test data |
| `EvaluationItem` | Per-image prediction/IoU/failure data |
| `ModelDeployment` | Active model assignment by project/workflow/stage |
| `PredictionCache` | Cached predictions for model+image+prompt |

Current `ModelVersion` can remain the top-level public object, but internally it
should point to training/evaluation/deployment records.

## Fine-tuning improvements for fruit defects

Fruit defect use cases need more than generic polygon LoRA.

Important capabilities:

1. **Approved-label-only training**
   - Default training source should be images completed in review workflow.
   - Avoid training on rejected/in-progress labels.

2. **Small-object optimization**
   - Defects may be tiny.
   - Need crop/patch sampling around annotations.
   - Track small-object IoU separately.

3. **Class imbalance handling**
   - Mold/rot may be rare.
   - Add oversampling for rare classes.
   - Show class count warnings before training.

4. **Hard-negative mining**
   - Include healthy fruit regions with no defect.
   - Reduce false positives.

5. **Augmentation presets**
   - color jitter
   - brightness/contrast
   - blur
   - crop/scale
   - rotation
   - background variation

6. **Failure gallery**
   - Show where base failed and fine-tuned improved/worsened.
   - Reviewer can mark "good", "bad", "needs relabel".

7. **Human-in-the-loop retraining**
   - Model predicts uncertain/low-confidence cases.
   - Those enter review queue.
   - Approved corrections feed next model version.

## Fine-tuning workflow integration

Paid workflow should allow stages like:

```txt
Dataset
  -> Model Assist: strawberry-defect-v2
  -> Annotator fixes prediction
  -> Reviewer
  -> Complete
```

Or:

```txt
Dataset
  -> AI Review
    accepted -> Complete
    suspicious -> Human Review
```

Model deployment should be scoped:

| Scope | Example |
| --- | --- |
| Project-wide active model | all auto-annotate uses v2 |
| Dataset-specific model | strawberry dataset uses v2 |
| Workflow-stage model | Model Assist stage uses v2 |
| User override | ML engineer compares v1/v2 manually |

## Paid feature packaging

### OSS features to keep

- Local setup
- Projects/datasets/classes
- Manual annotation tools
- SAM2 auto annotation
- Simple image review status
- Basic comments
- COCO export
- Basic model version list if we want, or keep training disabled by default

### Paid features

| Area | Paid feature |
| --- | --- |
| Teams | orgs, project memberships, multiple roles |
| Workflow | canvas builder, stages, routing |
| Assignment | queues, claim/reassign, workload dashboard |
| Review | multi-stage review, second review, consensus |
| Quality | dashboards, audit logs, issue analytics |
| Fine-tuning | model studio, snapshots, eval, deployment |
| Automation | AI review, webhooks, rules |
| Scale | background worker controls, job queue, storage backends |

## Suggested development order

### Milestone 1 - Paid foundation

Goal: turn current single-user app into flaggable OSS/Pro product.

Deliverables:

- Feature flag service
- Edition-aware nav
- Project membership schema
- Multi-role permissions
- Audit event table
- Admin/team management UI
- Migration path from `User.role` to memberships

### Milestone 2 - Review Queue MVP

Goal: teams can assign and review work without workflow canvas yet.

Deliverables:

- Work item table
- Assignment table
- My Queue page
- Assign/reassign bulk action
- Review queue filters
- Per-user workload counts
- Keep current Workview submit/approve/reject UI

### Milestone 3 - Workflow Engine

Goal: make review flows configurable.

Deliverables:

- Workflow/stage/edge schema
- Stage types: Dataset, Annotate, Review, Complete
- Accepted/rejected routing
- Stage assignee pools
- Stage-level self-review policy
- Backfill default workflow for existing projects

### Milestone 4 - Workflow Builder UI

Goal: V7-inspired paid workflow canvas.

Deliverables:

- Canvas with draggable stages
- Stage palette
- Node config panel
- Save/apply workflow
- Item count per node
- Bottom preview drawer

### Milestone 5 - Advanced Review

Goal: quality-management layer.

Deliverables:

- Second-review stage
- Consensus stage
- Rejection issue threads
- Batch approve/reject
- Review analytics
- Audit log UI

### Milestone 6 - Fine-tuning Studio MVP

Goal: make current model training production-like.

Deliverables:

- Training dataset snapshots
- Approved-label-only dataset builder
- Training run queue/logs
- Better model cards
- Compare base vs candidate
- Deploy/rollback active model

### Milestone 7 - Fine-tuning for defects

Goal: make it strong for fruit/defect use cases.

Deliverables:

- Crop/patch sampling
- Class imbalance warnings
- Rare-class oversampling
- Hard-negative support
- Augmentation presets
- Small-object metrics
- Visual failure gallery

### Milestone 8 - AI Review and Active Learning

Goal: close the loop.

Deliverables:

- AI review workflow stage
- Model confidence/uncertainty queues
- Automatic issue creation
- Active learning recommendations
- Retrain-from-corrections loop

## Recommendation

Start with **Review Queue MVP before Workflow Builder**. The canvas is
impressive, but the core paid value is assignment, review accountability, and
auditability. Build the data model and queue first, then put the V7-like visual
workflow builder on top.

For fine-tuning, start by making the current `ModelVersion` system more
trustworthy: immutable training snapshots, approved-label-only training, eval
gallery, deploy/rollback. Then optimize for fruit-defect segmentation with
crops, imbalance handling, and hard negatives.
