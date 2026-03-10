import type { SVGProps } from "react";
import { Icons } from "../icons.tsx";

type EmptyStateProps = {
  icon?: (p?: SVGProps<SVGSVGElement>) => React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
};

export default function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  const renderIcon = icon ?? Icons.inbox;
  return (
    <div className="empty-state">
      <div className="empty-state__icon">{renderIcon({ width: "2.5em", height: "2.5em" })}</div>
      <h3 className="empty-state__title">{title}</h3>
      {description && <p className="empty-state__desc">{description}</p>}
      {action && <div className="empty-state__action">{action}</div>}
    </div>
  );
}
