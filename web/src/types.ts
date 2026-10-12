export type TaskState =
  | "queued"
  | "running"
  | "validating"
  | "done"
  | "failed"
  | "paused"
  | "stopped_by_user"
  | "discarded";

export interface RoleBinding {
  node_id: number | null;
  model: string;
}

export interface Project {
  id: number;
  name: string;
  repo_url: string;
  default_branch: string;
  milestone_branch: string;
  test_cmd: string;
  executor_kind: string;
  role_bindings: Record<string, RoleBinding>;
  paused: boolean;
  push_after_merge: boolean;
  created_at: number;
  task_counts?: Partial<Record<TaskState, number>>;
}

export interface TaskRun {
  id: number;
  task_id: number;
  node_id: number | null;
  model: string;
  handle: string;
  state: "running" | "succeeded" | "failed" | "stopped" | "interrupted";
  exit_code: number | null;
  reason: string;
  started_at: number;
  ended_at: number | null;
}

export interface Task {
  id: number;
  project_id: number;
  title: string;
  description: string;
  todos: string[];
  allowed_globs: string[];
  validation_cmd: string;
  deps: number[];
  executor_kind: string;
  node_id: number | null;
  model: string;
  executor_script: string;
  timeout_s: number;
  state: TaskState;
  hold: boolean;
  reason: string;
  attempts: number;
  created_at: number;
  updated_at: number;
  runs?: TaskRun[];
  stop_pending?: boolean;
}

export interface Node {
  id: number;
  name: string;
  base_url: string;
  has_api_key: boolean;
  capacity: number;
  models: string[];
  healthy: boolean | null;
  last_checked: number | null;
  error?: string;
}

export interface FEvent {
  id: number;
  project_id: number | null;
  task_id: number | null;
  run_id: number | null;
  kind: "log" | "state" | "test" | "diff" | "control" | "system";
  message: string;
  data: Record<string, unknown>;
  ts: number;
}

export interface SystemInfo {
  estop: boolean;
  runtime: string;
  heartbeats: Record<string, number>;
  executors: string[];
}
