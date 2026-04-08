"use client"

/**
 * Auth Token Dialog Component
 *
 * Displays a modal dialog for users to manually enter their Quix Cloud authentication token
 * when the app is running in standalone mode (not embedded in Quix Portal iframe).
 *
 * Features:
 * - Non-dismissible modal (user must enter valid token to proceed)
 * - Password input for security
 * - Validation feedback
 * - Helper text with instructions
 */

import { useState } from "react"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useQuixAuth } from "@/lib/contexts/quix-auth-context"
import { AlertCircle } from "lucide-react"

export function AuthTokenDialog() {
  const { showAuthDialog, authError, handleTokenSubmit } = useQuixAuth()
  const [tokenInput, setTokenInput] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!tokenInput.trim()) {
      return
    }

    setIsSubmitting(true)
    try {
      await handleTokenSubmit(tokenInput.trim())
      // On success, dialog will close automatically and tokenInput will be cleared
      setTokenInput("")
    } catch (error) {
      // Error is handled by context and shown via authError
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog open={showAuthDialog} onOpenChange={() => {/* Non-dismissible */}}>
      <DialogContent
        className="sm:max-w-[500px]"
        hideCloseButton={true}
      >
        <DialogHeader>
          <DialogTitle>Authentication Required</DialogTitle>
          <DialogDescription>
            This app is running in standalone mode. Please enter your Quix Cloud authentication token to continue.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit}>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <label htmlFor="token" className="text-sm font-medium">
                Quix Cloud Token (PAT or Temporary)
              </label>
              <Input
                id="token"
                type="password"
                placeholder="Enter your authentication token"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                disabled={isSubmitting}
                autoFocus
                required
              />
              <p className="text-xs text-muted-foreground">
                Get your Personal Access Token from Quix Portal: Settings → Personal Access Tokens
              </p>
            </div>

            {authError && (
              <div className="flex items-start gap-2 p-3 text-sm text-destructive bg-destructive/10 rounded-md border border-destructive/20">
                <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  <p className="font-medium">Authentication Failed</p>
                  <p className="text-xs mt-1">{authError}</p>
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button
              type="submit"
              disabled={!tokenInput.trim() || isSubmitting}
              loading={isSubmitting}
            >
              {isSubmitting ? "Validating..." : "Authenticate"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
