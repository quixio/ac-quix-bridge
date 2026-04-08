"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
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
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"
import { useFilesApi } from "@/lib/hooks/use-api"
import { filesApi } from "@/lib/api/files"
import type { File } from "@/types/test"
import {
  FileText,
  FileImage,
  FileSpreadsheet,
  FileVideo,
  FileAudio,
  FileArchive,
  FileCode,
  FileIcon,
  Download,
  Trash2,
} from "lucide-react"

interface FileListProps {
  testId: string
  files: File[]
  onFileDeleted: () => void
}

function getFileIcon(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase()

  switch (ext) {
    case "pdf":
    case "doc":
    case "docx":
    case "txt":
      return FileText
    case "jpg":
    case "jpeg":
    case "png":
    case "gif":
    case "svg":
    case "webp":
      return FileImage
    case "xls":
    case "xlsx":
    case "csv":
      return FileSpreadsheet
    case "mp4":
    case "mov":
    case "avi":
    case "mkv":
      return FileVideo
    case "mp3":
    case "wav":
    case "ogg":
      return FileAudio
    case "zip":
    case "rar":
    case "7z":
    case "tar":
    case "gz":
      return FileArchive
    case "js":
    case "ts":
    case "jsx":
    case "tsx":
    case "py":
    case "java":
    case "cpp":
    case "c":
    case "h":
    case "json":
    case "xml":
    case "yaml":
    case "yml":
      return FileCode
    default:
      return FileIcon
  }
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B"
  const k = 1024
  const sizes = ["B", "KB", "MB", "GB"]
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`
}

export function FileList({ testId, files, onFileDeleted }: FileListProps) {
  const filesApiHook = useFilesApi()
  const [deletingFileId, setDeletingFileId] = useState<string | null>(null)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const { toast } = useToast()
  const { formatDateWithMonth } = useDateFormatter()

  const handleDownload = (file: File) => {
    const downloadUrl = filesApi.getDownloadUrl(testId, file.id)
    window.open(downloadUrl, "_blank")
  }

  const handleDeleteClick = (fileId: string) => {
    setDeletingFileId(fileId)
    setShowDeleteDialog(true)
  }

  const handleDeleteConfirm = async () => {
    if (!deletingFileId) return

    try {
      await filesApiHook.delete(testId, deletingFileId)
      toast({
        title: "File deleted",
        description: "The file has been deleted successfully.",
      })
      onFileDeleted()
    } catch (error) {
      toast({
        title: "Error deleting file",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setShowDeleteDialog(false)
      setDeletingFileId(null)
    }
  }

  if (files.length === 0) {
    return (
      <Card className="p-8 text-center">
        <FileIcon className="h-12 w-12 text-muted-foreground mx-auto mb-3" />
        <p className="text-sm text-muted-foreground">No files uploaded yet</p>
      </Card>
    )
  }

  // Sort files by uploaded_at descending (newest first)
  const sortedFiles = [...files].sort((a, b) =>
    new Date(b.uploaded_at).getTime() - new Date(a.uploaded_at).getTime()
  )

  const fileToDelete = files.find((f) => f.id === deletingFileId)

  return (
    <>
      <div className="space-y-2">
        {sortedFiles.map((file) => {
          const Icon = getFileIcon(file.name)
          return (
            <Card key={file.id} className="p-3">
              <div className="flex items-start gap-3">
                <Icon className="h-5 w-5 text-muted-foreground mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{file.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatFileSize(file.size)} • Uploaded {formatDateWithMonth(file.uploaded_at)}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleDownload(file)}
                  >
                    <Download className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleDeleteClick(file.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </Card>
          )
        })}
      </div>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete file?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete <strong>{fileToDelete?.name}</strong>?
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
