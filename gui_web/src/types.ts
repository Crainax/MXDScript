export type ScriptStatus = "idle" | "running" | "paused" | "stopping" | "finished" | "error";
export type ScriptOptionValue = boolean | number | string;

export interface ScriptItem {
  id: string;
  name: string;
  category: string;
  description: string;
  module: string;
  defaultShortcut: string;
  defaultOptions: Record<string, ScriptOptionValue>;
  placeholder: boolean;
  requiresMousePrecision: boolean;
  status: ScriptStatus;
  logPath: string | null;
  lastResult: Record<string, unknown> | null;
}

export interface RuntimeState {
  scripts: ScriptItem[];
  activeScriptId: string | null;
  logDir: string;
}

export interface AppSettings {
  shortcuts: Record<string, string>;
  scriptOptions: Record<string, Record<string, ScriptOptionValue>>;
  theme: ThemePreference;
  dryRun: boolean;
  skipDelays: boolean;
}

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export interface AppState {
  app: {
    title: string;
    version: string;
    logDir: string;
  };
  runtime: RuntimeState;
  settings: AppSettings;
}

export interface RuntimeEvent {
  type: "log" | "state" | "finished" | "error" | "data";
  scriptId: string;
  state?: ScriptStatus;
  level?: string;
  message?: string;
  logPath?: string;
  result?: Record<string, unknown>;
  payload?: Record<string, unknown>;
}

export interface LogLine {
  id: number;
  scriptId: string;
  level: string;
  message: string;
}
