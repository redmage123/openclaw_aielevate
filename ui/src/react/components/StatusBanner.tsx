import { useGatewayStore } from "../stores/gateway.ts";

/**
 * Shows a dismissible banner when the gateway is disconnected.
 * Rendered inside Layout, above the main content area.
 */
export default function StatusBanner() {
  const connected = useGatewayStore((s) => s.connected);
  const lastError = useGatewayStore((s) => s.lastError);

  if (connected) return null;

  return (
    <div className="status-banner status-banner--danger" role="alert">
      <span className="status-banner__text">
        {lastError
          ? `Disconnected: ${lastError}`
          : "Not connected to gateway. Attempting to reconnect..."}
      </span>
    </div>
  );
}
