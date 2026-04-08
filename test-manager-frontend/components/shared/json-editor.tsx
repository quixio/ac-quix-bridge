"use client"

import { useState, useEffect, useRef } from "react"
import dynamic from "next/dynamic"
import { Skeleton } from "@/components/ui/skeleton"

// Lazy load Monaco Editor to reduce initial bundle size (~2-3MB)
const Editor = dynamic(() => import("@monaco-editor/react"), {
  loading: () => <Skeleton className="h-[300px] w-full" />,
  ssr: false,
})

interface JsonEditorProps {
  value: string
  onChange: (value: string) => void
  height?: string
  readOnly?: boolean
}

export function JsonEditor({ value, onChange, height = "300px", readOnly = false }: JsonEditorProps) {
  const [error, setError] = useState<string | null>(null)
  const editorRef = useRef<any | null>(null)
  const lastExternalValueRef = useRef<string>(value)

  // Handle external value changes (e.g., form reset or external update)
  useEffect(() => {
    if (!editorRef.current) return

    const currentEditorValue = editorRef.current.getValue()

    // Only update editor if external value truly changed and differs from current content
    if (value !== lastExternalValueRef.current && value !== currentEditorValue) {
      const model = editorRef.current.getModel()
      if (model) {
        // Save cursor position
        const position = editorRef.current.getPosition()

        // Update value
        editorRef.current.setValue(value)

        // Restore cursor position if it's still valid
        if (position) {
          const lineCount = model.getLineCount()
          const lastLineLength = model.getLineLength(lineCount)

          // Ensure position is within bounds
          const validPosition = {
            lineNumber: Math.min(position.lineNumber, lineCount),
            column: position.lineNumber <= lineCount
              ? Math.min(position.column, model.getLineLength(position.lineNumber) + 1)
              : lastLineLength + 1
          }

          editorRef.current.setPosition(validPosition)
        }
      }

      lastExternalValueRef.current = value
    }
  }, [value])

  const handleEditorDidMount = (editor: any) => {
    editorRef.current = editor
    lastExternalValueRef.current = value
  }

  const handleChange = (newValue: string | undefined) => {
    if (newValue === undefined) return

    // Validate JSON
    try {
      if (newValue.trim()) {
        JSON.parse(newValue)
      }
      setError(null)
      onChange(newValue)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid JSON")
      // Still update the value so user can continue typing
      onChange(newValue)
    }
  }

  return (
    <div className="space-y-2">
      <div className="border rounded-md overflow-hidden">
        <Editor
          height={height}
          defaultLanguage="json"
          defaultValue={value}
          onChange={handleChange}
          onMount={handleEditorDidMount}
          theme="vs-dark"
          options={{
            readOnly,
            minimap: { enabled: false },
            fontSize: 13,
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            automaticLayout: true,
            tabSize: 2,
            formatOnPaste: true,
            formatOnType: true,
            scrollbar: {
              vertical: "auto",
              horizontal: "auto",
              verticalScrollbarSize: 10,
              horizontalScrollbarSize: 10,
              useShadows: false,
            },
          }}
        />
      </div>
      {error && (
        <p className="text-sm text-destructive">{error}</p>
      )}
    </div>
  )
}
