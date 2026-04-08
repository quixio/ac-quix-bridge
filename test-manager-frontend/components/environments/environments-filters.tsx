"use client"

import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { X } from "lucide-react"
import { useToast } from "@/lib/hooks/use-toast"

export function EnvironmentsFilters() {
  const { toast } = useToast()

  const handleFilterInteraction = () => {
    toast({
      title: "Feature under construction",
      description: "Filters will be functional when this feature is complete.",
    })
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {/* Search */}
        <Input
          placeholder="Search environments..."
          disabled
          className="cursor-not-allowed opacity-60"
          onClick={handleFilterInteraction}
        />

        {/* Status Filter */}
        <Select disabled onOpenChange={handleFilterInteraction}>
          <SelectTrigger className="cursor-not-allowed opacity-60">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="maintenance">Maintenance</SelectItem>
            <SelectItem value="inactive">Inactive</SelectItem>
          </SelectContent>
        </Select>

        {/* Location Filter */}
        <Input
          placeholder="Location..."
          disabled
          className="cursor-not-allowed opacity-60"
          onClick={handleFilterInteraction}
        />

        {/* Capacity Filter */}
        <Input
          placeholder="Min capacity..."
          type="number"
          disabled
          className="cursor-not-allowed opacity-60"
          onClick={handleFilterInteraction}
        />

        {/* Clear Filters Button */}
        <Button
          variant="outline"
          onClick={handleFilterInteraction}
          disabled
          className="cursor-not-allowed opacity-60"
        >
          <X className="mr-2 h-4 w-4" />
          Clear Filters
        </Button>
      </div>
    </div>
  )
}
