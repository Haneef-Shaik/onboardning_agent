/** One run: live status, its tasks, why a task is blocked, the controls HR has, and the trace link (UI-5). */
import type { ApiClient, Role, RunRecord, RunTaskRecord } from "@onb/ts-api-client";
import { Button } from "@onb/ts-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ErrorNotice } from "../components/ErrorNotice";
import { canControlRuns, canOpenTrace, controlsFor, traceUrl, whyBlocked, type RunControl } from "./policy";
import {
  cancelRun,
  completeTask,
  getRun,
  pauseRun,
  resumeRun,
  retryTask,
} from "./runs-api";
import { ChatPanel } from "./chat/ChatPanel";
import { StatusBadge } from "./StatusBadge";
import { useRunStream } from "./useRunStream";

const CONTROL_CALLS: Record<RunControl, (client: ApiClient, runId: string) => Promise<RunRecord>> = {
  pause: pauseRun,
  resume: resumeRun,
  cancel: cancelRun,
};

const CONTROL_LABELS: Record<RunControl, string> = { pause: "Pause", resume: "Resume", cancel: "Cancel run" };

export interface RunDetailProps {
  client: ApiClient;
  role: Role;
  runId: string;
  /** Jaeger's address (through the proxy); the trace link is built on it. */
  jaegerBase: string;
  retryDelayMs?: (attempt: number) => number;
}

export function RunDetail({ client, role, runId, jaegerBase, retryDelayMs }: RunDetailProps) {
  const queryClient = useQueryClient();
  const key = ["run", runId] as const;
  const run = useQuery({ queryKey: key, queryFn: () => getRun(client, runId) });
  const stream = useRunStream(client, runId, {
    onChange: () => void queryClient.invalidateQueries({ queryKey: key }),
    ...(retryDelayMs === undefined ? {} : { retryDelayMs }),
  });
  const act = useMutation({
    mutationFn: (call: () => Promise<RunRecord>) => call(),
    onSuccess: (updated) => {
      queryClient.setQueryData(key, updated);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  if (run.isError) return <ErrorNotice error={run.error} />;
  if (!run.data) return <p className="text-sm text-slate-500">Loading…</p>;
  const data = run.data;
  const staff = canControlRuns(role);
  const trace = canOpenTrace(role) ? traceUrl(jaegerBase, data.trace_id) : null;
  const blocked = data.tasks.filter((task) => task.status === "failed");

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Run {data.id}</h1>
        <StatusBadge status={data.status} testId="run-status" />
        <span data-testid="stream-state" className="text-xs text-slate-500">
          {stream.state === "live" ? "Live" : stream.state === "ended" ? "Finished" : "Connecting…"}
        </span>
        {trace && (
          <a href={trace} target="_blank" rel="noopener noreferrer" className="text-sm underline">
            Open trace
          </a>
        )}
      </header>
      <p className="text-sm text-slate-600">
        Hire {data.hire_id} · template {data.template_key} v{data.template_version}
      </p>

      {act.isError && <ErrorNotice error={act.error} />}

      {staff && controlsFor(data.status).length > 0 && (
        <div className="flex gap-2" role="group" aria-label="Run controls">
          {controlsFor(data.status).map((control) => (
            <Button
              key={control}
              variant={control === "cancel" ? "outline" : "default"}
              disabled={act.isPending}
              onClick={() => act.mutate(() => CONTROL_CALLS[control](client, runId))}
            >
              {CONTROL_LABELS[control]}
            </Button>
          ))}
        </div>
      )}

      {data.status === "blocked" && (
        <p role="status" className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          This run is blocked: {blocked.length === 1 ? "one task has" : `${blocked.length} tasks have`} failed. Retry or
          complete {blocked.length === 1 ? "it" : "them"} to move the run on.
        </p>
      )}

      <table aria-label="Tasks" className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs text-slate-500">
            <th className="py-1 pr-4 font-medium">Task</th>
            <th className="py-1 pr-4 font-medium">Status</th>
            <th className="py-1 pr-4 font-medium">Attempts</th>
            <th className="py-1 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {data.tasks.map((task) => (
            <TaskRow key={task.task_id} task={task} staff={staff} busy={act.isPending} client={client} runId={runId} act={act.mutate} />
          ))}
        </tbody>
      </table>

      <ChatPanel
        client={client}
        role={role}
        runId={runId}
        onRunChanged={() => void queryClient.invalidateQueries({ queryKey: key })}
      />

      <section aria-label="Activity">
        <h2 className="text-sm font-medium">Activity</h2>
        {stream.activity.length === 0 ? (
          <p className="text-xs text-slate-500">Nothing yet.</p>
        ) : (
          <ol data-testid="activity" className="mt-1 text-xs text-slate-600">
            {stream.activity.map((entry) => (
              <li key={entry.key}>
                {entry.taskId ?? "run"}: {entry.from} → {entry.to}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

interface TaskRowProps {
  task: RunTaskRecord;
  staff: boolean;
  busy: boolean;
  client: ApiClient;
  runId: string;
  act: (call: () => Promise<RunRecord>) => void;
}

function TaskRow({ task, staff, busy, client, runId, act }: TaskRowProps) {
  const [completing, setCompleting] = useState(false);
  const [reason, setReason] = useState("");
  const failed = task.status === "failed";

  function submit(event: FormEvent) {
    event.preventDefault();
    act(() => completeTask(client, runId, task.task_id, reason.trim()));
    setCompleting(false);
    setReason("");
  }

  return (
    <tr
      id={`task-${task.task_id}`}
      tabIndex={-1}
      data-testid={`task-${task.task_id}`}
      data-status={task.status}
      className="border-t border-slate-100 align-top focus:bg-amber-50 focus:outline-none"
    >
      <td className="py-1 pr-4">
        {task.title}
        {failed && (
          <p data-testid="blocked-reason" className="text-xs text-red-700">
            {whyBlocked(task.error)}
          </p>
        )}
        {task.skip_reason && <p className="text-xs text-slate-500">Skipped: {task.skip_reason}</p>}
      </td>
      <td className="py-1 pr-4">
        <StatusBadge status={task.status} />
      </td>
      <td className="py-1 pr-4">{task.attempts}</td>
      <td className="py-1">
        {staff && failed && !completing && (
          <div className="flex gap-2">
            <Button disabled={busy} onClick={() => act(() => retryTask(client, runId, task.task_id))}>
              Retry
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => setCompleting(true)}>
              Complete…
            </Button>
          </div>
        )}
        {staff && failed && completing && (
          <form onSubmit={submit} className="flex flex-col gap-1">
            <label className="text-xs" htmlFor={`reason-${task.task_id}`}>
              Reason for completing {task.title}
            </label>
            <input
              id={`reason-${task.task_id}`}
              className="rounded border border-slate-300 px-2 py-1 text-sm"
              value={reason}
              maxLength={500}
              onChange={(event) => setReason(event.target.value)}
            />
            <p className="text-xs text-slate-500">Kept in the audit log. Do not write personal details.</p>
            <div className="flex gap-2">
              <Button type="submit" disabled={busy || reason.trim() === ""}>
                Confirm complete
              </Button>
              <Button type="button" variant="outline" onClick={() => setCompleting(false)}>
                Cancel
              </Button>
            </div>
          </form>
        )}
      </td>
    </tr>
  );
}
