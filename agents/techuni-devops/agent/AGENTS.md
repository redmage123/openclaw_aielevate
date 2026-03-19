
## Ops Notification

Notify operations of significant events:
  from ops_notify import ops_notify
  ops_notify("event_type", "description", agent="techuni-devops", customer_email="customer@email")

Types: new_project, sentiment_drop, payment_received, payment_overdue, blocker, delivery_ready, asset_received, stale, escalation, customer_complaint, status_update, project_complete

## Preview Deployment

  from preview_deploy import deploy_preview, list_previews, teardown_preview, promote_to_production
  result = deploy_preview(project_dir="/path", slug="name", org="techuni", customer_email="email", production_domain="domain.com")
  promote_to_production("slug", "domain.com")
