"use client"

import * as React from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { DayPicker } from "react-day-picker"

import { cn } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"

export type CalendarProps = React.ComponentProps<typeof DayPicker>

function Calendar({
  className,
  classNames,
  showOutsideDays = true,
  ...props
}: CalendarProps) {
  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      weekStartsOn={1}
      className={cn("p-3", className)}
      classNames={{
        months: "flex flex-col sm:flex-row space-y-4 sm:space-x-4 sm:space-y-0",
        month: "space-y-2",
        caption: "flex justify-between pt-1 relative items-center px-2",
        caption_label: "text-sm font-medium",
        nav: "flex items-center gap-1",
        nav_button: cn(
          buttonVariants({ variant: "outline" }),
          "h-7 w-7 bg-transparent p-0 opacity-50 hover:opacity-100"
        ),
        nav_button_previous: "order-first",
        nav_button_next: "order-last",
        table: "w-full border-collapse space-y-1",
        weekdays: "grid grid-cols-7",
        weekday:
          "text-muted-foreground rounded-md w-10 font-normal text-[0.8rem] flex items-center justify-center",
        week: "grid grid-cols-7 gap-1 mt-2",
        cell: "h-10 w-10 text-center text-sm p-0 relative [&:has([aria-selected].day-outside)]:bg-accent/50 focus-within:relative focus-within:z-20",
        day: "h-10 w-10 text-center text-sm p-0 relative",
        day_button: cn(
          // Base button styles
          "inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium",
          "ring-offset-background transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          "disabled:pointer-events-none disabled:opacity-50",
          // Calendar-specific sizing
          "h-10 w-10 p-0 font-normal",
          // Thin white ring on hover for unselected days
          "hover:ring-1 hover:ring-white",
          // Selected state styling with thicker ring on hover
          "aria-selected:opacity-100 aria-selected:bg-primary aria-selected:text-primary-foreground",
          "aria-selected:hover:ring-2"
        ),
        selected:
          "rounded-md bg-primary text-primary-foreground focus:bg-primary focus:text-primary-foreground",
        day_today: "bg-accent text-accent-foreground",
        day_outside:
          "day-outside text-muted-foreground opacity-50 aria-selected:bg-accent/50 aria-selected:text-muted-foreground aria-selected:opacity-30",
        day_disabled: "text-muted-foreground opacity-50",
        day_hidden: "invisible",
        ...classNames,
      }}
      components={{
        Chevron: ({ orientation }: { orientation: "left" | "right" }) =>
          orientation === "left" ?
            <ChevronLeft className="h-4 w-4" /> :
            <ChevronRight className="h-4 w-4" />
      } as any}
      {...props}
    />
  )
}
Calendar.displayName = "Calendar"

export { Calendar }
