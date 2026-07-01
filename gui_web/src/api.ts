import type { AppSettings, AppState, RuntimeEvent, RuntimeState, ScriptOptionValue } from "./types";

interface ApiResponse {
  ok: boolean;
  error?: string;
  app?: AppState["app"];
  runtime?: RuntimeState;
  settings?: AppSettings;
  scriptData?: Record<string, Record<string, unknown>>;
  events?: RuntimeEvent[];
  path?: string;
}

const API_BASE = resolveApiBase();

export async function getState(): Promise<AppState> {
  const response = await requestJson("/state");
  if (!response.ok || !response.app || !response.runtime || !response.settings) {
    throw new Error(response.error ?? "读取应用状态失败");
  }
  return {
    app: response.app,
    runtime: normalizeRuntime(response.runtime),
    settings: response.settings,
    scriptData: response.scriptData ?? {},
  };
}

export async function pollEvents(): Promise<RuntimeEvent[]> {
  const response = await requestJson("/events");
  if (!response.ok) {
    throw new Error(response.error ?? "读取事件失败");
  }
  return response.events ?? [];
}

export async function startScript(
  scriptId: string,
  options: { dryRun: boolean; skipDelays: boolean },
): Promise<RuntimeState> {
  const response = await requestJson("/start", {
    method: "POST",
    body: JSON.stringify({ scriptId, options }),
  });
  return runtimeFromResponse(response);
}

export async function pauseScript(): Promise<RuntimeState> {
  return runtimeFromResponse(await requestJson("/pause", { method: "POST" }));
}

export async function resumeScript(): Promise<RuntimeState> {
  return runtimeFromResponse(await requestJson("/resume", { method: "POST" }));
}

export async function stopScript(): Promise<RuntimeState> {
  return runtimeFromResponse(await requestJson("/stop", { method: "POST" }));
}

export async function saveShortcuts(shortcuts: Record<string, string>): Promise<AppSettings> {
  const response = await requestJson("/shortcuts", {
    method: "POST",
    body: JSON.stringify({ shortcuts }),
  });
  if (!response.ok || !response.settings) {
    throw new Error(response.error ?? "保存快捷键失败");
  }
  return response.settings;
}

export async function saveRunOptions(options: { dryRun: boolean; skipDelays: boolean }): Promise<AppSettings> {
  const response = await requestJson("/settings", {
    method: "POST",
    body: JSON.stringify(options),
  });
  if (!response.ok || !response.settings) {
    throw new Error(response.error ?? "保存运行选项失败");
  }
  return response.settings;
}

export async function saveScriptOptions(
  scriptId: string,
  options: Record<string, ScriptOptionValue>,
): Promise<AppSettings> {
  const response = await requestJson("/script-options", {
    method: "POST",
    body: JSON.stringify({ scriptId, options }),
  });
  if (!response.ok || !response.settings) {
    throw new Error(response.error ?? "保存脚本配置失败");
  }
  return response.settings;
}

export async function openLogDir(): Promise<void> {
  const response = await requestJson("/open-log-dir", { method: "POST" });
  if (!response.ok) {
    throw new Error(response.error ?? "打开日志目录失败");
  }
}

export async function openPath(path: string): Promise<void> {
  const response = await requestJson("/open-path", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    throw new Error(response.error ?? "打开路径失败");
  }
}

export async function selectDirectory(initialPath: string): Promise<string> {
  const response = await requestJson("/select-directory", {
    method: "POST",
    body: JSON.stringify({ initialPath }),
  });
  if (!response.ok || typeof response.path !== "string") {
    throw new Error(response.error ?? "选择文件夹失败");
  }
  return response.path;
}

async function requestJson(path: string, init: RequestInit = {}): Promise<ApiResponse> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });

  const payload = (await response.json()) as ApiResponse;
  if (!response.ok && payload.ok !== false) {
    throw new Error(`HTTP ${response.status}`);
  }
  return payload;
}

function runtimeFromResponse(response: ApiResponse): RuntimeState {
  if (!response.ok || !response.runtime) {
    throw new Error(response.error ?? "运行时操作失败");
  }
  return normalizeRuntime(response.runtime);
}

function normalizeRuntime(runtime: RuntimeState): RuntimeState {
  return {
    ...runtime,
    scripts: runtime.scripts.map((script) => ({
      ...script,
      defaultShortcut:
        script.defaultShortcut ?? (script as unknown as { default_shortcut?: string }).default_shortcut ?? "",
      defaultOptions:
        script.defaultOptions ??
        (script as unknown as { default_options?: Record<string, ScriptOptionValue> }).default_options ??
        {},
      requiresMousePrecision:
        script.requiresMousePrecision ??
        (script as unknown as { requires_mouse_precision?: boolean }).requires_mouse_precision ??
        false,
    })),
  };
}

function resolveApiBase(): string {
  const apiFromQuery = new URLSearchParams(window.location.search).get("api");
  if (apiFromQuery) {
    return apiFromQuery.replace(/\/$/, "");
  }
  return `${window.location.origin}/api`;
}
