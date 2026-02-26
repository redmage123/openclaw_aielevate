export const CONTROL_UI_BOOTSTRAP_CONFIG_PATH = "/__openclaw/control-ui-config.json";

export type ControlUiBootstrapConfig = {
  basePath: string;
  assistantName: string;
  assistantAvatar: string;
  assistantAgentId: string;
  /** Auth mode for the control UI. Absent means single-user (legacy). */
  authMode?: "single-user" | "multi-user";
};
