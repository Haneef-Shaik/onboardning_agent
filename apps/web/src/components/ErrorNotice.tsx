import { describeError, type ErrorContext } from "../errors";

/** Shows an API error's user-facing text, the fields it names, and the trace id for support. */
export function ErrorNotice({ error, context = "default" }: { error: unknown; context?: ErrorContext }) {
  const described = describeError(error, context);
  return (
    <div role="alert" className="rounded-md bg-red-50 p-3 text-sm text-red-900">
      <p>{described.message}</p>
      {described.fields.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {described.fields.map((field) => (
            <li key={field.field}>
              {field.field}: {field.issue}
            </li>
          ))}
        </ul>
      )}
      {described.traceId && <p className="mt-1 text-xs text-red-700">Reference: {described.traceId}</p>}
    </div>
  );
}
