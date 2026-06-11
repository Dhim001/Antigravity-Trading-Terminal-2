"use client"

import * as React from "react"
import { cva } from "class-variance-authority";
import { Toggle as TogglePrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

const toggleVariants = cva(
  "group/toggle inline-flex items-center justify-center gap-[var(--icon-gap)] rounded-md text-[0.82rem] font-medium whitespace-nowrap transition-all outline-none hover:bg-muted hover:text-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 aria-pressed:bg-muted data-[state=on]:bg-muted dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      variant: {
        default: "bg-transparent",
        outline: "border border-input bg-transparent hover:bg-muted",
        buy:
          "border border-transparent data-[state=on]:border-[color-mix(in_srgb,var(--color-up)_50%,transparent)] data-[state=on]:bg-[var(--color-up-bg)] data-[state=on]:text-[var(--color-up)] data-[state=on]:shadow-[0_0_10px_var(--color-up-dim)]",
        sell:
          "border border-transparent data-[state=on]:border-[color-mix(in_srgb,var(--color-down)_50%,transparent)] data-[state=on]:bg-[var(--color-down-bg)] data-[state=on]:text-[var(--color-down)] data-[state=on]:shadow-[0_0_10px_var(--color-down-dim)]",
      },
      size: {
        default:
          "h-[var(--control-h)] min-w-[var(--control-h)] px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        sm: "h-7 min-w-7 rounded-[var(--r-md)] px-2 text-[0.75rem] has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3",
        lg: "h-9 min-w-9 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Toggle({
  className,
  variant = "default",
  size = "default",
  ...props
}) {
  return (
    <TogglePrimitive.Root
      data-slot="toggle"
      className={cn(toggleVariants({ variant, size, className }))}
      {...props} />
  );
}

export { Toggle, toggleVariants }
