"use client";

import * as React from "react";
import { Check, ChevronsUpDown, X } from "lucide-react";
import { useDebouncedCallback } from "use-debounce";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

export interface ComboboxOption {
  value: string;
  label: string;
}

interface ComboboxProps {
  options: ComboboxOption[];
  value?: string;
  onValueChange: (value: string | undefined) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  className?: string;
  disabled?: boolean;
  allowCustomValue?: boolean;
}

export function Combobox({
  options,
  value,
  onValueChange,
  placeholder = "Select option...",
  searchPlaceholder = "Search...",
  emptyText = "No results found.",
  className,
  disabled = false,
  allowCustomValue = false,
}: ComboboxProps) {
  const [open, setOpen] = React.useState(false);
  const [searchValue, setSearchValue] = React.useState("");

  const selectedOption = options.find((option) => option.value === value);

  // Display value: show selected option label, or custom value, or placeholder
  const displayValue =
    selectedOption?.label || (allowCustomValue && value) || placeholder;

  // Check if current value is a custom filter (not in options list)
  const isCustomValue = allowCustomValue && value && !selectedOption;

  // Debounced callback for custom typed values (300ms)
  const debouncedCustomValue = useDebouncedCallback((val: string) => {
    if (allowCustomValue) {
      onValueChange(val || undefined);
    }
  }, 300);

  // Reset search when closing
  React.useEffect(() => {
    if (!open) {
      setSearchValue("");
    }
  }, [open]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className={cn("w-full justify-between px-3 font-normal", className)}
          disabled={disabled}
        >
          <span className="truncate">{displayValue}</span>
          {value ? (
            <X
              className="ml-2 h-4 w-4 shrink-0 opacity-70 hover:opacity-100 transition-opacity"
              onClick={(e) => {
                e.stopPropagation();
                onValueChange(undefined);
              }}
            />
          ) : (
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[var(--radix-popover-trigger-width)] p-0"
        align="start"
      >
        <Command>
          <CommandInput
            placeholder={searchPlaceholder}
            onValueChange={(search) => {
              setSearchValue(search);
              // When allowCustomValue, debounce the typed value to parent
              if (allowCustomValue) {
                debouncedCustomValue(search);
              }
            }}
          />
          <CommandList>
            <CommandEmpty>
              {allowCustomValue && searchValue ? (
                <div className="py-6 text-center text-sm text-muted-foreground">
                  Using &ldquo;{searchValue}&rdquo; for partial search
                </div>
              ) : (
                emptyText
              )}
            </CommandEmpty>
            <CommandGroup>
              {options.map((option) => (
                <CommandItem
                  key={option.value}
                  value={option.value}
                  onSelect={(currentValue) => {
                    // Use option.value directly to avoid lowercase conversion issue
                    // When user clicks/selects, call parent immediately (no debounce)
                    onValueChange(
                      option.value === value ? undefined : option.value,
                    );
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn(
                      "mr-2 h-4 w-4",
                      value === option.value ? "opacity-100" : "opacity-0",
                    )}
                  />
                  {option.label}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
