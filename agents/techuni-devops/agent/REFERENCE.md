# techuni-devops — Reference Documentation

This file is loaded by the agent when needed. Do not put critical rules here.

## Ops Notification

Notify operations of significant events:
  from ops_notify import ops_notify
  ops_notify("event_type", "description", agent="techuni-devops", customer_email="customer@email")

Types: new_project, sentiment_drop, payment_received, payment_overdue, blocker, delivery_ready, asset_received, stale, escalation, customer_complaint, status_update, project_complete


## Preview Deployment

  from preview_deploy import deploy_preview, list_previews, teardown_preview, promote_to_production
  result = deploy_preview(project_dir="/path", slug="name", org="techuni", customer_email="email", production_domain="domain.com")
  promote_to_production("slug", "domain.com")



## Plane Integration

Track all work in Plane:
```
from plane_ops import Plane
p = Plane("techuni")
# Create deployment tickets
p.create_issue(project="WEB", title="[DEPLOY] Project Name", description="Details", priority="high")
# Update ticket when deployment completes
p.add_comment(project="WEB", issue_sequence_id=N, comment="Deployed to preview: URL")
```
