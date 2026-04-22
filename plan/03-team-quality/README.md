# Phase 3 — Team & Quality

Multi-user labelling and review.

## Tasks

### Membership
- [ ] `Org` (single-org for v0; schema ready for many)
- [ ] `Membership` (user_id, org_id, role: owner/admin/labeler/reviewer/viewer)
- [ ] Project visibility (org-wide vs. specific members)
- [ ] Invite flow (email link with signup token)

### Review flow
- [ ] Annotation status: `draft → submitted → approved/rejected`
- [ ] `Review` model (annotation_id, reviewer_id, decision, reject_reason_code, comment, point_xy)
- [ ] Project-scoped predefined reject reasons (admin manages)
- [ ] Reviewer Workview variant: same canvas + Approve/Reject/Comment toolbar
- [ ] **Spatial comments**: click a point to leave a comment pinned to image coords
- [ ] Per-image timer captured into DB on Send-to-Review (V7 has this)

### Quality dashboard
- [ ] `Quality` page per project
- [ ] Per-annotator: images/hour sparkline, rejection rate, avg per-image time
- [ ] Per-class: IoU agreement matrix between annotators (pick 50 images both labeled, compute pairwise IoU)
- [ ] Rejection-reason histogram

### Notifications (light)
- [ ] In-app inbox: "Image X rejected, fix and resubmit"
- [ ] No email for v0

## Acceptance
- 2 labeler accounts + 1 reviewer can collaborate on one dataset without seeing each other's in-progress edits
- Reject → spatial comment → labeler fixes → resubmit → approve loop is <3 clicks
