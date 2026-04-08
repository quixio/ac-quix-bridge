"use client"

import { useState, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { useToast } from "@/lib/hooks/use-toast"
import { useFilesApi } from "@/lib/hooks/use-api"
import { Upload, X, FileIcon } from "lucide-react"

interface FileUploadManagerProps {
  testId: string
  onUploadComplete: () => void
}

interface UploadingFile {
  file: File
  progress: number
  error?: string
}

export function FileUploadManager({ testId, onUploadComplete }: FileUploadManagerProps) {
  const filesApi = useFilesApi()
  const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const { toast } = useToast()

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return

    const fileArray = Array.from(files)

    // Add files to uploading state
    const newUploadingFiles = fileArray.map((file) => ({
      file,
      progress: 0,
    }))
    setUploadingFiles((prev) => [...prev, ...newUploadingFiles])

    // Upload each file
    for (let i = 0; i < fileArray.length; i++) {
      const file = fileArray[i]
      const fileIndex = uploadingFiles.length + i

      try {
        // Get presigned upload URL
        const { url } = await filesApi.getPresignedUploadUrl(testId, file.name)

        // Upload file with progress tracking
        await filesApi.uploadFile(url, file, (progress) => {
          setUploadingFiles((prev) => {
            const updated = [...prev]
            if (updated[fileIndex]) {
              updated[fileIndex] = { ...updated[fileIndex], progress }
            }
            return updated
          })
        })

        // Mark as complete
        setUploadingFiles((prev) => {
          const updated = [...prev]
          if (updated[fileIndex]) {
            updated[fileIndex] = { ...updated[fileIndex], progress: 100 }
          }
          return updated
        })

        toast({
          title: "File uploaded",
          description: `${file.name} was uploaded successfully.`,
        })
      } catch (error) {
        console.error("Upload error:", error)
        const errorMessage = error instanceof Error ? error.message : "Upload failed"

        setUploadingFiles((prev) => {
          const updated = [...prev]
          if (updated[fileIndex]) {
            updated[fileIndex] = { ...updated[fileIndex], error: errorMessage }
          }
          return updated
        })

        toast({
          title: "Upload failed",
          description: `Failed to upload ${file.name}: ${errorMessage}`,
          variant: "destructive",
        })
      }
    }

    // Clear completed uploads after a delay
    setTimeout(() => {
      setUploadingFiles((prev) => prev.filter((uf) => uf.progress < 100 && !uf.error))
      onUploadComplete()
    }, 2000)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    handleFiles(e.dataTransfer.files)
  }

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    handleFiles(e.target.files)
    // Reset input so same file can be uploaded again
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
  }

  const handleRemoveUpload = (index: number) => {
    setUploadingFiles((prev) => prev.filter((_, i) => i !== index))
  }

  return (
    <div className="space-y-3">
      {/* Drop Zone */}
      <Card
        className={`border-2 border-dashed transition-colors ${
          isDragging
            ? "border-primary bg-primary/5"
            : "border-muted-foreground/25 hover:border-muted-foreground/50"
        }`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <div className="p-8 text-center">
          <Upload className="mx-auto h-12 w-12 text-muted-foreground mb-4" />
          <p className="text-sm text-muted-foreground mb-4">
            Drag and drop files here, or click to browse
          </p>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={handleFileInputChange}
            className="hidden"
          />
          <Button
            variant="outline"
            onClick={() => fileInputRef.current?.click()}
          >
            Choose Files
          </Button>
        </div>
      </Card>

      {/* Uploading Files */}
      {uploadingFiles.length > 0 && (
        <div className="space-y-2">
          {uploadingFiles.map((uf, index) => (
            <Card key={index} className="p-3">
              <div className="flex items-start gap-3">
                <FileIcon className="h-5 w-5 text-muted-foreground mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-sm font-medium truncate">
                      {uf.file.name}
                    </p>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto p-1 -mr-2"
                      onClick={() => handleRemoveUpload(index)}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground mb-2">
                    {(uf.file.size / 1024 / 1024).toFixed(2)} MB
                  </p>
                  {uf.error ? (
                    <p className="text-xs text-destructive">{uf.error}</p>
                  ) : (
                    <div className="space-y-1">
                      <Progress value={uf.progress} className="h-1" />
                      <p className="text-xs text-muted-foreground">
                        {uf.progress === 100 ? "Complete" : `${uf.progress}%`}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
