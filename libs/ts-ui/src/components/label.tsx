import { Root } from "@radix-ui/react-label";
import type { ComponentProps } from "react";
import { cn } from "../cn";

/** shadcn/ui Label. */
export function Label({ className, ...props }: ComponentProps<typeof Root>) {
  return <Root className={cn("text-sm font-medium leading-none", className)} {...props} />;
}
