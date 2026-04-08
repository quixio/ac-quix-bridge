/**
 * API client for File Management
 * Provides methods to interact with /tests/{test_id}/files endpoints
 */

import { apiGet, apiPost, apiDelete, getApiUrl } from "./client"
import type { File as FileMetadata } from "@/types/test"

export interface PresignedUploadRequest {
  filename: string
}

export interface PresignedUploadResponse {
  url: string
}

export const filesApi = {
  /**
   * Get presigned URL for uploading a file
   */
  getPresignedUploadUrl: (
    testId: string,
    filename: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPost<PresignedUploadResponse>(
      `/tests/${testId}/files`,
      { filename },
      token,
      refreshToken
    )
  },

  /**
   * Upload file to presigned URL with progress tracking
   * @param uploadUrl - The presigned URL from getPresignedUploadUrl
   * @param file - The file to upload
   * @param onProgress - Optional callback for upload progress (0-100)
   *
   * Note: This doesn't use auth token - uploads directly to storage
   */
  uploadFile: (
    uploadUrl: string,
    file: File,
    onProgress?: (progress: number) => void
  ): Promise<void> => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()

      // Track upload progress
      if (onProgress) {
        xhr.upload.addEventListener("progress", (e) => {
          if (e.lengthComputable) {
            const progress = Math.round((e.loaded / e.total) * 100)
            onProgress(progress)
          }
        })
      }

      // Handle completion
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve()
        } else {
          reject(new Error(`Upload failed with status ${xhr.status}`))
        }
      })

      // Handle errors
      xhr.addEventListener("error", () => {
        reject(new Error("Upload failed due to network error"))
      })

      xhr.addEventListener("abort", () => {
        reject(new Error("Upload was aborted"))
      })

      // Send the file
      xhr.open("PUT", uploadUrl)
      xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream")
      xhr.send(file)
    })
  },

  /**
   * List all files for a test
   */
  list: (
    testId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<FileMetadata[]>(`/tests/${testId}/files`, undefined, token, refreshToken)
  },

  /**
   * Get a single file by ID
   */
  get: (
    testId: string,
    fileId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<FileMetadata>(`/tests/${testId}/files/${fileId}`, undefined, token, refreshToken)
  },

  /**
   * Get download URL for a file
   * Note: This returns a URL string, not an authenticated request
   */
  getDownloadUrl: (testId: string, fileId: string) => {
    const apiUrl = getApiUrl()
    return `${apiUrl}/tests/${testId}/files/${fileId}/download`
  },

  /**
   * Delete a file
   */
  delete: (
    testId: string,
    fileId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiDelete(`/tests/${testId}/files/${fileId}`, token, refreshToken)
  },
}
