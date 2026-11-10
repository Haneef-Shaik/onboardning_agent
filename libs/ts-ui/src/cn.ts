import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Join class names, dropping falsy values and resolving Tailwind conflicts (shadcn/ui convention). */
export function cn(...classes: ClassValue[]): string {
  return twMerge(clsx(classes));
}
