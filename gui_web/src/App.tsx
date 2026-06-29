import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  FolderOpen,
  Keyboard,
  Moon,
  Monitor,
  Pause,
  Play,
  RotateCcw,
  Square,
  Sun,
  SquareTerminal,
} from "lucide-react";
import {
  getState,
  openLogDir,
  openPath,
  pauseScript,
  pollEvents,
  resumeScript,
  saveRunOptions,
  saveScriptOptions,
  saveShortcuts,
  startScript,
  stopScript,
} from "./api";
import type {
  AppSettings,
  AppState,
  LogLine,
  ResolvedTheme,
  RuntimeEvent,
  RuntimeState,
  ScriptItem,
  ScriptOptionValue,
  ThemePreference,
} from "./types";

const MAX_LOG_LINES = 800;
const DAILY_SCRIPT_ID = "daily_script";
const DAILY_OPTION_ITEMS = [
  { key: "dailyQuest", label: "日常任务" },
  { key: "gugu", label: "菇菇神社" },
  { key: "summerDaily", label: "活动签到" },
  { key: "otherDaily", label: "其他每日" },
] as const;

export function App() {
  const [appState, setAppState] = useState<AppState | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [selectedScriptId, setSelectedScriptId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editingShortcutId, setEditingShortcutId] = useState<string | null>(null);
  const [themePreference, setThemePreference] = useState<ThemePreference>("system");
  const [dryRun, setDryRun] = useState(false);
  const [skipDelays, setSkipDelays] = useState(false);
  const [optionsLoaded, setOptionsLoaded] = useState(false);
  const logId = useRef(1);

  useEffect(() => {
    void getState()
      .then((state) => {
        setAppState(state);
        setSelectedScriptId(state.runtime.scripts[0]?.id ?? null);
        const storedTheme = readStoredTheme(state.settings.theme);
        setThemePreference(storedTheme);
        setDryRun(readStoredBoolean("mxdscript.dryRun", state.settings.dryRun));
        setSkipDelays(readStoredBoolean("mxdscript.skipDelays", state.settings.skipDelays));
        setOptionsLoaded(true);
      })
      .catch((error: unknown) => {
        setMessage(error instanceof Error ? error.message : "启动 GUI 失败");
      });
  }, []);

  const resolvedTheme = useResolvedTheme(themePreference);
  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.style.colorScheme = resolvedTheme;
    localStorage.setItem("mxdscript.theme", themePreference);
  }, [resolvedTheme, themePreference]);

  useEffect(() => {
    if (!optionsLoaded) {
      return;
    }
    localStorage.setItem("mxdscript.dryRun", String(dryRun));
    localStorage.setItem("mxdscript.skipDelays", String(skipDelays));
    void saveRunOptions({ dryRun, skipDelays })
      .then((nextSettings) => {
        setAppState((current) => (current ? { ...current, settings: nextSettings } : current));
      })
      .catch((error: unknown) => {
        setMessage(error instanceof Error ? error.message : "保存运行选项失败");
      });
  }, [dryRun, optionsLoaded, skipDelays]);

  const runtime = appState?.runtime;
  const settings = appState?.settings;
  const selectedScript = useMemo(
    () => runtime?.scripts.find((script) => script.id === selectedScriptId) ?? runtime?.scripts[0] ?? null,
    [runtime?.scripts, selectedScriptId],
  );
  const activeScript = useMemo(
    () => runtime?.scripts.find((script) => script.id === runtime.activeScriptId) ?? null,
    [runtime?.activeScriptId, runtime?.scripts],
  );
  const visibleLogs = useMemo(
    () => (selectedScript ? logs.filter((line) => line.scriptId === selectedScript.id) : logs),
    [logs, selectedScript],
  );
  const selectedScriptOptions = useMemo(
    () => (selectedScript && settings ? scriptOptionsFor(settings, selectedScript) : {}),
    [selectedScript, settings],
  );

  const applyRuntime = useCallback((nextRuntime: RuntimeState) => {
    setAppState((current) => (current ? { ...current, runtime: nextRuntime } : current));
  }, []);

  const appendLog = useCallback((event: RuntimeEvent) => {
    if (event.type !== "log" || !event.message) {
      return;
    }
    const line: LogLine = {
      id: logId.current++,
      scriptId: event.scriptId,
      level: event.level ?? "INFO",
      message: event.message,
    };
    setLogs((current) => [...current.slice(-MAX_LOG_LINES + 1), line]);
  }, []);

  const mergeEvent = useCallback((event: RuntimeEvent) => {
    appendLog(event);
    if (event.type === "state" && event.state) {
      setAppState((current) => {
        if (!current) {
          return current;
        }
        return {
          ...current,
          runtime: {
            ...current.runtime,
            activeScriptId:
              event.state === "running" || event.state === "paused" || event.state === "stopping"
                ? event.scriptId
                : current.runtime.activeScriptId === event.scriptId
                  ? null
                  : current.runtime.activeScriptId,
            scripts: current.runtime.scripts.map((script) =>
              script.id === event.scriptId
                ? {
                    ...script,
                    status: event.state ?? script.status,
                    logPath: event.logPath ?? script.logPath,
                  }
                : script,
            ),
          },
        };
      });
    }
    if (event.type === "finished") {
      setMessage("脚本已结束。");
    }
    if (event.type === "error" && event.message) {
      setMessage(event.message);
    }
  }, [appendLog]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void pollEvents()
        .then((events) => {
          events.forEach(mergeEvent);
        })
        .catch((error: unknown) => {
          setMessage(error instanceof Error ? error.message : "读取日志事件失败");
        });
    }, 350);
    return () => window.clearInterval(timer);
  }, [mergeEvent]);

  const runStart = useCallback(
    async (scriptId: string) => {
      try {
        const nextRuntime = await startScript(scriptId, { dryRun, skipDelays });
        applyRuntime(nextRuntime);
        setSelectedScriptId(scriptId);
        setMessage(null);
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "启动脚本失败");
      }
    },
    [applyRuntime, dryRun, skipDelays],
  );

  const runPause = useCallback(async () => {
    try {
      applyRuntime(await pauseScript());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "暂停脚本失败");
    }
  }, [applyRuntime]);

  const runResume = useCallback(async () => {
    try {
      applyRuntime(await resumeScript());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "继续脚本失败");
    }
  }, [applyRuntime]);

  const runStop = useCallback(async () => {
    try {
      applyRuntime(await stopScript());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "停止脚本失败");
    }
  }, [applyRuntime]);

  const updateSelectedScriptOption = useCallback(
    async (key: string, value: ScriptOptionValue) => {
      if (!selectedScript || !settings) {
        return;
      }
      const nextOptions = {
        ...scriptOptionsFor(settings, selectedScript),
        [key]: value,
      };
      try {
        const nextSettings = await saveScriptOptions(selectedScript.id, nextOptions);
        setAppState((current) => (current ? { ...current, settings: nextSettings } : current));
        setMessage(null);
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "保存脚本配置失败");
      }
    },
    [selectedScript, settings],
  );

  const handleShortcutCapture = useCallback(
    async (scriptId: string, event: ReactKeyboardEvent<HTMLButtonElement>) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.nativeEvent.key === "Backspace") {
        if (!settings) {
          return;
        }
        try {
          const nextSettings = await saveShortcuts({ ...settings.shortcuts, [scriptId]: "" });
          setAppState((current) => (current ? { ...current, settings: nextSettings } : current));
          setEditingShortcutId(null);
          setMessage("已清除快捷键。");
        } catch (error) {
          setMessage(error instanceof Error ? error.message : "保存快捷键失败");
        }
        return;
      }
      if (event.nativeEvent.key === "Escape") {
        setMessage("Esc 不能作为启动快捷键。");
        return;
      }
      const shortcut = shortcutFromKeyboardEvent(event.nativeEvent, { allowEsc: false });
      if (!shortcut) {
        setMessage("启动快捷键需要使用 F1-F12，或 Ctrl/Alt/Shift 搭配字母、数字、F键。");
        return;
      }
      if (shortcut === "Esc") {
        setMessage("Esc 不能作为启动快捷键。");
        return;
      }
      if (!settings) {
        return;
      }
      try {
        const nextSettings = await saveShortcuts({ ...settings.shortcuts, [scriptId]: shortcut });
        setAppState((current) => (current ? { ...current, settings: nextSettings } : current));
        setEditingShortcutId(null);
        setMessage(null);
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "保存快捷键失败");
      }
    },
    [settings],
  );

  const updateTheme = (theme: ThemePreference) => {
    setThemePreference(theme);
  };

  if (!appState || !runtime || !settings) {
    return (
      <main className="app app-loading">
        <div className="loading-panel">
          <SquareTerminal size={22} />
          <span>{message ?? "正在启动 MXD脚本库..."}</span>
        </div>
      </main>
    );
  }

  return (
    <main className="app">
      <aside className="sidebar">
        <section className="brand-panel">
          <div className="brand-title">
            <SquareTerminal size={22} />
            <span>脚本</span>
          </div>
          <ThemeSwitch value={themePreference} resolved={resolvedTheme} onChange={updateTheme} />
        </section>

        <ScriptList
          scripts={runtime.scripts}
          shortcuts={settings.shortcuts}
          selectedScriptId={selectedScript?.id ?? null}
          activeScriptId={runtime.activeScriptId}
          editingShortcutId={editingShortcutId}
          onSelect={setSelectedScriptId}
          onEditShortcut={setEditingShortcutId}
          onShortcutKeyDown={handleShortcutCapture}
        />
      </aside>

      <section className="main-area">
        <header className="top-bar">
          <div>
            <div className="title-row">
              <h1>{appState.app.title}</h1>
              <span className="version-badge">v{appState.app.version}</span>
            </div>
            <div className="subline">
              {activeScript ? `${activeScript.name} · ${statusText(activeScript.status)}` : "空闲"}
            </div>
          </div>
          <div className="mode-controls" aria-label="运行模式">
            <label className="toggle">
              <input type="checkbox" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} />
              <span>模拟</span>
            </label>
            <label className="toggle">
              <input
                type="checkbox"
                checked={skipDelays}
                onChange={(event) => setSkipDelays(event.target.checked)}
              />
              <span>跳过等待</span>
            </label>
          </div>
        </header>

        <section className="workspace">
          <section className="script-panel">
            {selectedScript ? (
              <ScriptControlPanel
                script={selectedScript}
                shortcut={settings.shortcuts[selectedScript.id] ?? selectedScript.defaultShortcut}
                activeScript={activeScript}
                onStart={() => void runStart(selectedScript.id)}
                onPause={() => void runPause()}
                onResume={() => void runResume()}
                onStop={() => void runStop()}
              />
            ) : null}
            {selectedScript?.id === DAILY_SCRIPT_ID ? (
              <DailyOptionsPanel options={selectedScriptOptions} onChange={updateSelectedScriptOption} />
            ) : null}
            {message ? (
              <div className="notice">
                <AlertCircle size={16} />
                <span>{message}</span>
              </div>
            ) : null}
          </section>

          <LogPanel
            logs={visibleLogs}
            selectedScript={selectedScript}
            logDir={runtime.logDir}
            onOpenLogDir={() => void openLogDir().catch((error) => setMessage(String(error)))}
            onOpenCurrentLog={() => {
              if (!selectedScript?.logPath) {
                setMessage("当前脚本还没有日志文件。");
                return;
              }
              void openPath(selectedScript.logPath).catch((error) => setMessage(String(error)));
            }}
            onClear={() => setLogs([])}
          />
        </section>
      </section>
    </main>
  );
}

function ScriptList(props: {
  scripts: ScriptItem[];
  shortcuts: Record<string, string>;
  selectedScriptId: string | null;
  activeScriptId: string | null;
  editingShortcutId: string | null;
  onSelect: (scriptId: string) => void;
  onEditShortcut: (scriptId: string | null) => void;
  onShortcutKeyDown: (scriptId: string, event: ReactKeyboardEvent<HTMLButtonElement>) => void;
}) {
  return (
    <section className="script-list">
      {props.scripts.map((script) => (
        <article
          key={script.id}
          className={`script-card ${script.id === props.selectedScriptId ? "selected" : ""}`}
          onClick={() => props.onSelect(script.id)}
        >
          <div className="script-card-main">
            <div>
              <div className="script-name-row">
                <span className="script-name">{script.name}</span>
                {script.placeholder ? <span className="pill muted">占位</span> : <span className="pill">可运行</span>}
              </div>
              <div className="script-meta">
                {script.category} · {statusText(script.status)}
              </div>
            </div>
            <StatusDot status={script.status} />
          </div>
          <div className="shortcut-row">
            <Keyboard size={14} />
            <button
              type="button"
              className="shortcut-button"
              title="设置启动快捷键"
              onClick={(event) => {
                event.stopPropagation();
                props.onEditShortcut(props.editingShortcutId === script.id ? null : script.id);
              }}
              onKeyDown={(event) => {
                if (props.editingShortcutId === script.id) {
                  props.onShortcutKeyDown(script.id, event);
                }
              }}
            >
              {props.editingShortcutId === script.id
                ? "按下快捷键"
                : shortcutLabel(props.shortcuts[script.id] ?? script.defaultShortcut)}
            </button>
          </div>
        </article>
      ))}
    </section>
  );
}

function ScriptControlPanel(props: {
  script: ScriptItem;
  shortcut: string;
  activeScript: ScriptItem | null;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
}) {
  const isActive = props.activeScript?.id === props.script.id;
  const canStart = !props.activeScript;
  const canPause = isActive && props.script.status === "running";
  const canResume = isActive && props.script.status === "paused";
  const canStop = isActive && (props.script.status === "running" || props.script.status === "paused");

  return (
    <div className="control-surface">
      <div className="script-heading">
        <div>
          <div className="category-label">{props.script.category}</div>
          <h2>{props.script.name}</h2>
        </div>
        <span className={`state-badge state-${props.script.status}`}>{statusText(props.script.status)}</span>
      </div>
      <p className="script-description">{props.script.description}</p>
      <dl className="script-details">
        <div>
          <dt>模块</dt>
          <dd>{props.script.module}</dd>
        </div>
        <div>
          <dt>启动快捷键</dt>
          <dd>{shortcutLabel(props.shortcut)}</dd>
        </div>
      </dl>
      <div className="button-row">
        <button type="button" className="primary-button" disabled={!canStart} onClick={props.onStart}>
          <Play size={17} />
          <span>开始</span>
        </button>
        <button type="button" className="tool-button" disabled={!canPause} onClick={props.onPause}>
          <Pause size={17} />
          <span>暂停</span>
        </button>
        <button type="button" className="tool-button" disabled={!canResume} onClick={props.onResume}>
          <RotateCcw size={17} />
          <span>继续</span>
        </button>
        <button type="button" className="danger-button" disabled={!canStop} onClick={props.onStop}>
          <Square size={16} />
          <span>停止</span>
        </button>
      </div>
    </div>
  );
}

function DailyOptionsPanel(props: {
  options: Record<string, ScriptOptionValue>;
  onChange: (key: string, value: ScriptOptionValue) => void;
}) {
  const onChange = props.onChange;
  const thresholdValue = optionNumber(props.options.matchThreshold, 0.95);
  const [thresholdText, setThresholdText] = useState(formatThreshold(thresholdValue));

  useEffect(() => {
    setThresholdText(formatThreshold(thresholdValue));
  }, [thresholdValue]);

  const commitThreshold = useCallback(() => {
    const nextValue = Number(thresholdText);
    if (!Number.isFinite(nextValue)) {
      setThresholdText(formatThreshold(thresholdValue));
      return;
    }
    const clampedValue = clampMatchThreshold(nextValue);
    setThresholdText(formatThreshold(clampedValue));
    onChange("matchThreshold", clampedValue);
  }, [onChange, thresholdText, thresholdValue]);

  return (
    <section className="daily-options-panel">
      <div className="panel-header">
        <div>
          <div className="category-label">脚本配置</div>
          <h2>日常模块</h2>
        </div>
      </div>
      <div className="daily-option-list">
        {DAILY_OPTION_ITEMS.map((item) => (
          <label className="daily-option" key={item.key}>
            <input
              type="checkbox"
              checked={optionBoolean(props.options[item.key], true)}
              onChange={(event) => props.onChange(item.key, event.target.checked)}
            />
            <span>{item.label}</span>
          </label>
        ))}
      </div>
      <label className="daily-threshold">
        <span>找图容忍值</span>
        <input
          type="text"
          inputMode="decimal"
          value={thresholdText}
          onBlur={commitThreshold}
          onChange={(event) => setThresholdText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.currentTarget.blur();
            }
          }}
        />
      </label>
    </section>
  );
}

function LogPanel(props: {
  logs: LogLine[];
  selectedScript: ScriptItem | null;
  logDir: string;
  onOpenLogDir: () => void;
  onOpenCurrentLog: () => void;
  onClear: () => void;
}) {
  const logStreamRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const logStream = logStreamRef.current;
    if (!logStream) {
      return;
    }
    logStream.scrollTop = logStream.scrollHeight;
  }, [props.logs.length]);

  return (
    <section className="log-panel">
      <div className="panel-header">
        <div>
          <div className="category-label">日志</div>
          <h2>实时输出</h2>
        </div>
        <div className="icon-buttons">
          <button type="button" className="icon-button" title="打开日志目录" onClick={props.onOpenLogDir}>
            <FolderOpen size={17} />
          </button>
          <button type="button" className="icon-button" title="打开当前日志" onClick={props.onOpenCurrentLog}>
            <FileText size={17} />
          </button>
          <button type="button" className="icon-button" title="清空显示" onClick={props.onClear}>
            <Square size={15} />
          </button>
        </div>
      </div>
      <div className="log-path">{props.selectedScript?.logPath ?? props.logDir}</div>
      <div className="log-stream" ref={logStreamRef}>
        {props.logs.length === 0 ? (
          <div className="empty-log">等待脚本输出...</div>
        ) : (
          props.logs.map((line) => (
            <div key={line.id} className={`log-line level-${line.level.toLowerCase()}`}>
              <span>{line.message}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function ThemeSwitch(props: {
  value: ThemePreference;
  resolved: ResolvedTheme;
  onChange: (theme: ThemePreference) => void;
}) {
  const options: Array<{ value: ThemePreference; label: string; icon: ReactNode }> = [
    { value: "system", label: "系统", icon: <Monitor size={14} /> },
    { value: "light", label: "浅色", icon: <Sun size={14} /> },
    { value: "dark", label: "深色", icon: <Moon size={14} /> },
  ];

  return (
    <div className="theme-switch" data-resolved={props.resolved}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={props.value === option.value ? "active" : ""}
          onClick={() => props.onChange(option.value)}
        >
          {option.icon}
          <span>{option.label}</span>
        </button>
      ))}
    </div>
  );
}

function StatusDot({ status }: { status: ScriptItem["status"] }) {
  const Icon = status === "finished" ? CheckCircle2 : status === "error" ? AlertCircle : null;
  return <span className={`status-dot status-${status}`}>{Icon ? <Icon size={13} /> : null}</span>;
}

function statusText(status: ScriptItem["status"]): string {
  const labels: Record<ScriptItem["status"], string> = {
    idle: "待命",
    running: "运行中",
    paused: "已暂停",
    stopping: "停止中",
    finished: "已结束",
    error: "异常",
  };
  return labels[status];
}

function useResolvedTheme(preference: ThemePreference): ResolvedTheme {
  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(() =>
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  );

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) {
      return;
    }
    const update = () => setSystemTheme(media.matches ? "dark" : "light");
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return preference === "system" ? systemTheme : preference;
}

function shortcutFromKeyboardEvent(
  event: KeyboardEvent,
  options: { allowEsc?: boolean } = {},
): string | null {
  if (event.key === "Escape") {
    return options.allowEsc ? "Esc" : null;
  }
  if (["Control", "Alt", "Shift", "Meta"].includes(event.key)) {
    return null;
  }

  const modifiers = [
    event.ctrlKey ? "Ctrl" : null,
    event.altKey ? "Alt" : null,
    event.shiftKey ? "Shift" : null,
  ].filter(Boolean) as string[];
  const key = normalizeEventKey(event.key);
  if (!key) {
    return null;
  }
  if (key.startsWith("F")) {
    return [...modifiers, key].join("+");
  }
  if (modifiers.length === 0) {
    return null;
  }
  return [...modifiers, key].join("+");
}

function normalizeEventKey(key: string): string | null {
  if (/^F([1-9]|1[0-2])$/.test(key)) {
    return key;
  }
  if (key.length === 1 && /[a-zA-Z0-9]/.test(key)) {
    return key.toUpperCase();
  }
  return null;
}

function shortcutLabel(shortcut: string): string {
  return shortcut.trim() ? shortcut : "未设置";
}

function scriptOptionsFor(settings: AppSettings, script: ScriptItem): Record<string, ScriptOptionValue> {
  return {
    ...script.defaultOptions,
    ...(settings.scriptOptions?.[script.id] ?? {}),
  };
}

function optionBoolean(value: ScriptOptionValue | undefined, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function optionNumber(value: ScriptOptionValue | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function clampMatchThreshold(value: number): number {
  return Math.min(1, Math.max(0.5, value));
}

function formatThreshold(value: number): string {
  return String(Number(value.toFixed(3)));
}

function readStoredTheme(fallback: ThemePreference): ThemePreference {
  const value = localStorage.getItem("mxdscript.theme");
  return value === "system" || value === "light" || value === "dark" ? value : fallback;
}

function readStoredBoolean(key: string, fallback: boolean): boolean {
  const value = localStorage.getItem(key);
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }
  return fallback;
}
