"use client"

import * as React from "react"
import { format } from "date-fns"
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Input } from "@/components/ui/input"

interface DateTimePickerProps {
  value?: Date
  onChange?: (date: Date | undefined) => void
  placeholder?: string
  disabled?: boolean
  className?: string
}

// Helper to strip time from date for calendar comparison
const getDateWithoutTime = (date: Date | undefined) => {
  if (!date) return undefined
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

export function DateTimePicker({
  value,
  onChange,
  placeholder = "Pick a date and time",
  disabled = false,
  className,
}: DateTimePickerProps) {
  const [timeValue, setTimeValue] = React.useState<string>(
    value ? format(value, "HH:mm") : "00:00"
  )
  const [currentMonth, setCurrentMonth] = React.useState<Date>(value || new Date())
  const [isOpen, setIsOpen] = React.useState(false)

  // Sync timeValue and currentMonth when value prop changes
  React.useEffect(() => {
    if (value) {
      setTimeValue(format(value, "HH:mm"))
      setCurrentMonth(value)
    }
  }, [value])

  // Memoize the selected date without time to maintain stable reference
  const selectedDate = React.useMemo(() => {
    return getDateWithoutTime(value)
  }, [value])

  const handleTimeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const time = e.target.value
    setTimeValue(time)

    if (!value || !time || time.includes("_")) {
      return
    }

    // Validate time format (HH:mm)
    const timeParts = time.split(":")
    if (timeParts.length !== 2) return

    const hours = parseInt(timeParts[0], 10)
    const minutes = parseInt(timeParts[1], 10)

    // Validate parsed values
    if (isNaN(hours) || isNaN(minutes) || hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
      return
    }

    const newDate = new Date(value)
    newDate.setHours(hours, minutes, 0, 0)

    onChange?.(newDate)
  }

  const handleDaySelect = (date: Date | undefined) => {
    if (!date) {
      onChange?.(undefined)
      return
    }

    // Validate and parse time
    const timeParts = timeValue.split(":")
    if (timeParts.length !== 2) {
      // Fallback to midnight if time is invalid
      const newDate = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0)
      onChange?.(newDate)
      return
    }

    const hours = parseInt(timeParts[0], 10)
    const minutes = parseInt(timeParts[1], 10)

    // Validate parsed values, fallback to midnight if invalid
    if (isNaN(hours) || isNaN(minutes) || hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
      const newDate = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0)
      onChange?.(newDate)
      return
    }

    // Valid time - use it
    const newDate = new Date(date.getFullYear(), date.getMonth(), date.getDate(), hours, minutes, 0, 0)
    onChange?.(newDate)
  }

  const handleClear = () => {
    setTimeValue("00:00")
    onChange?.(undefined)
  }

  const handlePreviousMonth = () => {
    const newMonth = new Date(currentMonth)
    newMonth.setMonth(newMonth.getMonth() - 1)
    setCurrentMonth(newMonth)
  }

  const handleNextMonth = () => {
    const newMonth = new Date(currentMonth)
    newMonth.setMonth(newMonth.getMonth() + 1)
    setCurrentMonth(newMonth)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      setIsOpen(false)
    }
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            className={cn(
              "w-full justify-start text-left font-normal",
              !value && "text-muted-foreground"
            )}
            disabled={disabled}
          >
            <CalendarIcon className="mr-2 h-4 w-4" />
            {value ? (
              format(value, "PPP HH:mm")
            ) : (
              <span>{placeholder}</span>
            )}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <div className="p-4">
            {/* Header with navigation and time */}
            <div className="flex items-center justify-between gap-4 pb-3 border-b">
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="icon"
                  className="h-7 w-7 bg-transparent p-0 opacity-50 hover:opacity-100"
                  onClick={handlePreviousMonth}
                  disabled={disabled}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <div className="text-sm font-medium min-w-[120px] text-center">
                  {format(currentMonth, "MMMM yyyy")}
                </div>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-7 w-7 bg-transparent p-0 opacity-50 hover:opacity-100"
                  onClick={handleNextMonth}
                  disabled={disabled}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
              <div className="flex items-center gap-2">
                <label htmlFor="time-picker" className="text-sm font-medium">
                  Time:
                </label>
                <Input
                  id="time-picker"
                  type="time"
                  value={timeValue}
                  onChange={handleTimeChange}
                  onKeyDown={handleKeyDown}
                  className="w-auto"
                  disabled={disabled}
                />
              </div>
            </div>

            {/* Calendar without caption */}
            <div className="flex justify-center py-3">
              <Calendar
                mode="single"
                month={currentMonth}
                onMonthChange={setCurrentMonth}
                selected={selectedDate}
                onSelect={handleDaySelect}
                initialFocus
                className="p-0"
                classNames={{
                  caption: "hidden",
                  caption_label: "hidden",
                  nav: "hidden",
                  month: "space-y-0",
                  table: "w-auto border-collapse space-y-1",
                }}
              />
            </div>

            <div className="pt-3 border-t">
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleClear}
                  disabled={disabled || !value}
                  className="w-24"
                >
                  Clear
                </Button>
                <Button
                  size="sm"
                  onClick={() => setIsOpen(false)}
                  disabled={disabled}
                  className="w-24"
                >
                  Apply
                </Button>
              </div>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}
