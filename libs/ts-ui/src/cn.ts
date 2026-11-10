/** Join class names, dropping falsy values (shadcn/ui convention). */
export function cn(...classes: ReadonlyArray<string | false | null | undefined>): string {
  return classes.filter((c): c is string => typeof c === "string" && c.length > 0).join(" ");
}
