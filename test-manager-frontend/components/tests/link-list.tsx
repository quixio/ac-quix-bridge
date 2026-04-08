"use client"

import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card } from "@/components/ui/card"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { useToast } from "@/lib/hooks/use-toast"
import { useLinksApi } from "@/lib/hooks/use-api"
import type { Link, LinkCreate } from "@/types/test"
import { ExternalLink, Trash2, Plus, X, Pencil } from "lucide-react"

interface LinkListProps {
  testId: string
  links: Link[]
  onLinkAdded: () => void
  onLinkDeleted: () => void
  onLinkUpdated: () => void
}

const linkSchema = z.object({
  label: z.string().min(1, "Label is required"),
  url: z.string().url("Must be a valid URL"),
})

type LinkFormData = z.infer<typeof linkSchema>

export function LinkList({ testId, links, onLinkAdded, onLinkDeleted, onLinkUpdated }: LinkListProps) {
  const linksApi = useLinksApi()
  const [showAddForm, setShowAddForm] = useState(false)
  const [editingLinkId, setEditingLinkId] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [deletingLinkId, setDeletingLinkId] = useState<string | null>(null)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const { toast } = useToast()

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<LinkFormData>({
    resolver: zodResolver(linkSchema),
  })

  const handleAddLink = async (data: LinkFormData) => {
    setIsSubmitting(true)
    try {
      if (editingLinkId) {
        // Update existing link
        await linksApi.update(testId, editingLinkId, data)
        toast({
          title: "Link updated",
          description: "The external link has been updated successfully.",
        })
        setEditingLinkId(null)
        onLinkUpdated()
      } else {
        // Create new link
        await linksApi.create(testId, data)
        toast({
          title: "Link added",
          description: "The external link has been added successfully.",
        })
        onLinkAdded()
      }
      reset()
      setShowAddForm(false)
    } catch (error) {
      toast({
        title: editingLinkId ? "Error updating link" : "Error adding link",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleEditClick = (link: Link, e: React.MouseEvent) => {
    e.stopPropagation()
    setShowAddForm(false)
    setEditingLinkId(link.id)
    reset({ label: link.label, url: link.url })
  }

  const handleCardClick = (url: string) => {
    window.open(url, "_blank")
  }

  const handleDeleteClick = (linkId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setDeletingLinkId(linkId)
    setShowDeleteDialog(true)
  }

  const handleDeleteConfirm = async () => {
    if (!deletingLinkId) return

    try {
      await linksApi.delete(testId, deletingLinkId)
      toast({
        title: "Link deleted",
        description: "The external link has been deleted successfully.",
      })
      onLinkDeleted()
    } catch (error) {
      toast({
        title: "Error deleting link",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setShowDeleteDialog(false)
      setDeletingLinkId(null)
    }
  }

  const linkToDelete = links.find((l) => l.id === deletingLinkId)

  return (
    <>
      <div className="space-y-2">
        {/* Existing Links */}
        {links.map((link) => {
          // If editing this link, show the edit form in its position
          if (editingLinkId === link.id) {
            return (
              <Card key={link.id} className="p-4">
                <form onSubmit={handleSubmit(handleAddLink)} className="space-y-3">
                  <div>
                    <Label htmlFor="label">Label *</Label>
                    <Input
                      id="label"
                      {...register("label")}
                      placeholder="e.g., Grafana Dashboard"
                    />
                    {errors.label && (
                      <p className="text-sm text-destructive mt-1">{errors.label.message}</p>
                    )}
                  </div>

                  <div>
                    <Label htmlFor="url">URL *</Label>
                    <Input
                      id="url"
                      {...register("url")}
                      placeholder="https://example.com"
                    />
                    {errors.url && (
                      <p className="text-sm text-destructive mt-1">{errors.url.message}</p>
                    )}
                  </div>

                  <div className="flex justify-end gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setEditingLinkId(null)
                        reset()
                      }}
                      disabled={isSubmitting}
                    >
                      <X className="h-4 w-4 mr-1" />
                      Cancel
                    </Button>
                    <Button type="submit" size="sm" disabled={isSubmitting}>
                      {isSubmitting ? "Updating..." : "Update Link"}
                    </Button>
                  </div>
                </form>
              </Card>
            )
          }

          // Otherwise show the link card
          return (
            <Card
              key={link.id}
              className="p-3 cursor-pointer hover:bg-muted/50 transition-colors"
              onClick={() => handleCardClick(link.url)}
            >
              <div className="flex items-start gap-3">
                <ExternalLink className="h-4 w-4 text-muted-foreground mt-1 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium">{link.label}</p>
                  <p className="text-xs text-muted-foreground truncate">{link.url}</p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation()
                      window.open(link.url, "_blank")
                    }}
                  >
                    <ExternalLink className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={(e) => handleEditClick(link, e)}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={(e) => handleDeleteClick(link.id, e)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </Card>
          )
        })}

        {/* Empty State */}
        {links.length === 0 && !showAddForm && (
          <Card className="p-6 text-center">
            <ExternalLink className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
            <p className="text-sm text-muted-foreground">No external links yet</p>
          </Card>
        )}

        {/* Add Link Form (only shown when not editing) */}
        {showAddForm && !editingLinkId ? (
          <Card className="p-4">
            <form onSubmit={handleSubmit(handleAddLink)} className="space-y-3">
              <div>
                <Label htmlFor="label">Label *</Label>
                <Input
                  id="label"
                  {...register("label")}
                  placeholder="e.g., Grafana Dashboard"
                />
                {errors.label && (
                  <p className="text-sm text-destructive mt-1">{errors.label.message}</p>
                )}
              </div>

              <div>
                <Label htmlFor="url">URL *</Label>
                <Input
                  id="url"
                  {...register("url")}
                  placeholder="https://example.com"
                />
                {errors.url && (
                  <p className="text-sm text-destructive mt-1">{errors.url.message}</p>
                )}
              </div>

              <div className="flex justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setShowAddForm(false)
                    reset()
                  }}
                  disabled={isSubmitting}
                >
                  <X className="h-4 w-4 mr-1" />
                  Cancel
                </Button>
                <Button type="submit" size="sm" disabled={isSubmitting}>
                  {isSubmitting ? "Adding..." : "Add Link"}
                </Button>
              </div>
            </form>
          </Card>
        ) : !editingLinkId ? (
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => {
              setEditingLinkId(null)
              reset()
              setShowAddForm(true)
            }}
          >
            <Plus className="h-4 w-4 mr-2" />
            Add Link
          </Button>
        ) : null}
      </div>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete link?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this link?
              {linkToDelete && (
                <div className="mt-2 p-2 bg-muted rounded text-sm">
                  <p className="font-medium">{linkToDelete.label}</p>
                  <p className="text-xs text-muted-foreground truncate">
                    {linkToDelete.url}
                  </p>
                </div>
              )}
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
