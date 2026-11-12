/**
 * The mock Telegram outbox (FR-N2): what the Telegram mock "sent", recipients masked. A message's Approve and
 * Reject buttons work for the user it is addressed to: the press goes to the API, which calls
 * `POST /webhooks/telegram` exactly as a real Telegram callback would (ADR 0026).
 */
import type { ApiClient, Me } from "@onb/ts-api-client";
import { Button } from "@onb/ts-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { ErrorNotice } from "../components/ErrorNotice";
import { ALREADY_DECIDED, isAlreadyDecided, listOutbox, pressOutboxButton, type MockOutboxMessage } from "./approvals-api";

const REFRESH_MS = 3000;

export function MockOutbox({ client, me }: { client: ApiClient; me: Me }) {
  const outbox = useQuery({ queryKey: ["mock-outbox"], queryFn: () => listOutbox(client), refetchInterval: REFRESH_MS });
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Mock Telegram outbox</h1>
      {outbox.isError && <ErrorNotice error={outbox.error} />}
      {outbox.data?.length === 0 && <p className="text-sm text-slate-500">No messages yet.</p>}
      {outbox.data?.map((message) => (
        <OutboxMessage key={message.id} client={client} me={me} message={message} />
      ))}
    </div>
  );
}

function OutboxMessage({ client, me, message }: { client: ApiClient; me: Me; message: MockOutboxMessage }) {
  const queryClient = useQueryClient();
  const press = useMutation({
    mutationFn: (callbackData: string) => pressOutboxButton(client, message.id, callbackData),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["approvals"] }),
  });
  const mine = message.audience_user_id === me.user_id;

  return (
    <article data-testid={`outbox-${message.id}`} className="rounded-md border border-slate-200 p-4">
      <p className="text-xs text-slate-500">
        To {message.recipient_masked} · <time dateTime={message.sent_at}>{new Date(message.sent_at).toLocaleString()}</time>
        {message.run_id && (
          <>
            {" "}
            · run{" "}
            <Link to={`/runs/${message.run_id}`} className="underline">
              {message.run_id}
            </Link>
          </>
        )}
      </p>
      <pre className="mt-2 whitespace-pre-wrap font-sans text-sm">{message.text}</pre>
      {message.buttons.length > 0 &&
        (mine ? (
          <div className="mt-3 flex gap-2">
            {message.buttons.map((button) => (
              <Button
                key={button.callback_data}
                variant={button.decision === "approve" ? "default" : "outline"}
                disabled={press.isPending || press.isSuccess}
                onClick={() => press.mutate(button.callback_data)}
              >
                {button.text}
              </Button>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-xs text-slate-500">Buttons addressed to someone else.</p>
        ))}
      {press.isSuccess && (
        <p role="status" className="mt-2 text-sm text-green-800">
          Sent to the Telegram webhook. The inbox shows whether it decided the approval.
        </p>
      )}
      {press.isError &&
        (isAlreadyDecided(press.error) ? (
          <p role="alert" className="mt-2 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
            {ALREADY_DECIDED}
          </p>
        ) : (
          <div className="mt-2">
            <ErrorNotice error={press.error} />
          </div>
        ))}
    </article>
  );
}
