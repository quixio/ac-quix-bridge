"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { usePortalApi } from "@/lib/hooks/use-api"
import type {
  Repository,
  WorkspaceDetails,
  DeploymentInfo,
  DeploymentReference,
} from "@/lib/types/portal"
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FolderOpen,
  Rocket,
  Search,
  Check,
  Loader2,
} from "lucide-react"
import { cn } from "@/lib/utils"

interface TreeNode {
  id: string
  name: string
  type: "repository" | "workspace" | "deployment"
  repositoryId?: string
  workspaceId?: string
  children: TreeNode[]
  isExpanded: boolean
  isLoading: boolean
  data?: Repository | WorkspaceDetails | DeploymentInfo
}

interface DeploymentPickerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedDeployment: DeploymentReference | null
  onConfirm: (deployment: DeploymentReference | null) => void
  isFallback?: boolean
  title?: string
  description?: string
}

export function DeploymentPickerDialog({
  open,
  onOpenChange,
  selectedDeployment,
  onConfirm,
  isFallback = false,
  title = "Select Deployment",
  description = "Navigate through projects and environments to select a deployment.",
}: DeploymentPickerDialogProps) {
  const portalApi = usePortalApi()

  // Tree state
  const [nodes, setNodes] = useState<TreeNode[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Search state
  const [searchQuery, setSearchQuery] = useState("")

  // Selection state
  const [selected, setSelected] = useState<DeploymentInfo | null>(null)
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null)

  // Track if current selection is the auto-detected/default one
  const [selectionIsFallback, setSelectionIsFallback] = useState(isFallback)

  // Remember the fallback deployment ID so we can recognize it when re-selected
  const fallbackDeploymentIdRef = useRef<string | null>(
    isFallback && selectedDeployment ? selectedDeployment.deployment_id : null
  )

  // Track if we've done initial expansion
  const hasExpandedRef = useRef(false)

  // Ref for scrolling to the selected item
  const selectedItemRef = useRef<HTMLDivElement | null>(null)

  // Track the saved (non-fallback) selection so we can badge it
  const savedSelectionRef = useRef<{ deploymentId: string; workspaceId: string } | null>(
    !isFallback && selectedDeployment
      ? { deploymentId: selectedDeployment.deployment_id, workspaceId: selectedDeployment.workspace_id }
      : null
  )

  // Load workspaces for a repository
  const loadWorkspacesForRepo = useCallback(
    async (repositoryId: string): Promise<TreeNode[]> => {
      try {
        const workspaces = await portalApi.getWorkspaces(repositoryId)
        return workspaces.map((ws) => ({
          id: ws.workspaceId,
          name: ws.environmentName || ws.name,
          type: "workspace" as const,
          repositoryId: ws.repositoryId,
          workspaceId: ws.workspaceId,
          children: [],
          isExpanded: false,
          isLoading: false,
          data: ws,
        }))
      } catch (err) {
        console.error("Failed to load workspaces:", err)
        return []
      }
    },
    [portalApi]
  )

  // Load deployments for a workspace
  const loadDeploymentsForWorkspace = useCallback(
    async (workspaceId: string): Promise<TreeNode[]> => {
      try {
        const deployments = await portalApi.getDeployments(workspaceId)
        return deployments.map((dep) => ({
          id: dep.deploymentId,
          name: dep.name,
          type: "deployment" as const,
          workspaceId: workspaceId,
          children: [],
          isExpanded: false,
          isLoading: false,
          data: dep,
        }))
      } catch (err) {
        console.error("Failed to load deployments:", err)
        return []
      }
    },
    [portalApi]
  )

  // Load repositories and auto-expand to selected deployment
  useEffect(() => {
    if (!open) {
      hasExpandedRef.current = false
      return
    }

    const loadAndExpand = async () => {
      setLoading(true)
      setError(null)
      hasExpandedRef.current = false

      try {
        const repositories = await portalApi.getRepositories()
        let treeNodes: TreeNode[] = repositories.map((repo) => ({
          id: repo.repositoryId,
          name: repo.name,
          type: "repository" as const,
          repositoryId: repo.repositoryId,
          children: [],
          isExpanded: false,
          isLoading: false,
          data: repo,
        }))

        // If we have a selected deployment, expand the tree to show it
        if (selectedDeployment && !hasExpandedRef.current) {
          hasExpandedRef.current = true

          // Fetch ALL workspaces in one call to find the target without iterating repos
          const allWorkspaces = await portalApi.getWorkspaces(undefined)
          const targetWs = allWorkspaces.find(ws => ws.workspaceId === selectedDeployment.workspace_id)

          if (targetWs) {
            // Build workspace nodes for the matching repo only
            const repoWorkspaceNodes: TreeNode[] = allWorkspaces
              .filter(ws => ws.repositoryId === targetWs.repositoryId)
              .map(ws => ({
                id: ws.workspaceId,
                name: ws.environmentName || ws.name,
                type: "workspace" as const,
                repositoryId: ws.repositoryId,
                workspaceId: ws.workspaceId,
                children: [],
                isExpanded: false,
                isLoading: false,
                data: ws,
              }))

            // Load deployments only for the target workspace
            const deployments = await loadDeploymentsForWorkspace(selectedDeployment.workspace_id)

            // Find the selected deployment
            const targetDeployment = deployments.find(d => d.id === selectedDeployment.deployment_id)
            if (targetDeployment) {
              setSelected(targetDeployment.data as DeploymentInfo)
              setSelectedWorkspaceId(selectedDeployment.workspace_id)
            }

            // Update workspace with deployments and mark as expanded
            const updatedWorkspaces = repoWorkspaceNodes.map(ws =>
              ws.workspaceId === selectedDeployment.workspace_id
                ? { ...ws, children: deployments, isExpanded: true }
                : ws
            )

            // Update repo with workspaces and mark as expanded
            treeNodes = treeNodes.map(r =>
              r.id === targetWs.repositoryId
                ? { ...r, children: updatedWorkspaces, isExpanded: true }
                : r
            )
          }
        }

        setNodes(treeNodes)
      } catch (err) {
        console.error("Failed to load repositories:", err)
        setError(err instanceof Error ? err.message : "Failed to load projects")
      } finally {
        setLoading(false)
      }
    }

    loadAndExpand()
  }, [open, selectedDeployment, portalApi, loadWorkspacesForRepo, loadDeploymentsForWorkspace])

  // Reset state when dialog closes, sync fallback when it opens
  useEffect(() => {
    if (!open) {
      setSelected(null)
      setSelectedWorkspaceId(null)
      setSearchQuery("")
    } else {
      setSelectionIsFallback(isFallback)
      fallbackDeploymentIdRef.current = isFallback && selectedDeployment
        ? selectedDeployment.deployment_id
        : null
      savedSelectionRef.current = !isFallback && selectedDeployment
        ? { deploymentId: selectedDeployment.deployment_id, workspaceId: selectedDeployment.workspace_id }
        : null
    }
  }, [open, isFallback, selectedDeployment])

  // Auto-scroll to the selected item after loading completes
  useEffect(() => {
    if (!loading && selectedItemRef.current) {
      requestAnimationFrame(() => {
        selectedItemRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })
      })
    }
  }, [loading])

  const toggleNode = useCallback(
    async (nodeId: string, path: string[]) => {
      setNodes((prevNodes) => {
        const updateNode = (nodes: TreeNode[], pathIndex: number): TreeNode[] => {
          return nodes.map((node) => {
            if (node.id === path[pathIndex]) {
              if (pathIndex === path.length - 1) {
                return { ...node, isExpanded: !node.isExpanded, isLoading: !node.isExpanded && node.children.length === 0 }
              } else {
                return { ...node, children: updateNode(node.children, pathIndex + 1) }
              }
            }
            return node
          })
        }
        return updateNode(prevNodes, 0)
      })

      // Find the node to check if we need to load children
      const findNode = (nodes: TreeNode[], targetId: string): TreeNode | null => {
        for (const node of nodes) {
          if (node.id === targetId) return node
          const found = findNode(node.children, targetId)
          if (found) return found
        }
        return null
      }

      const targetNode = findNode(nodes, nodeId)
      if (!targetNode || targetNode.isExpanded || targetNode.children.length > 0) {
        return
      }

      // Load children based on node type
      let children: TreeNode[] = []
      if (targetNode.type === "repository") {
        children = await loadWorkspacesForRepo(targetNode.id)
      } else if (targetNode.type === "workspace") {
        children = await loadDeploymentsForWorkspace(targetNode.id)
      }

      // Update the tree with loaded children
      setNodes((prevNodes) => {
        const updateNodeChildren = (nodes: TreeNode[], pathIndex: number): TreeNode[] => {
          return nodes.map((node) => {
            if (node.id === path[pathIndex]) {
              if (pathIndex === path.length - 1) {
                return { ...node, children, isLoading: false }
              } else {
                return { ...node, children: updateNodeChildren(node.children, pathIndex + 1) }
              }
            }
            return node
          })
        }
        return updateNodeChildren(prevNodes, 0)
      })
    },
    [nodes, loadWorkspacesForRepo, loadDeploymentsForWorkspace]
  )

  const handleSelectDeployment = useCallback(
    (deployment: DeploymentInfo, workspaceId: string) => {
      setSelected(deployment)
      setSelectedWorkspaceId(workspaceId)
      // Re-selecting the fallback item restores the blue indicator
      setSelectionIsFallback(
        fallbackDeploymentIdRef.current !== null &&
        deployment.deploymentId === fallbackDeploymentIdRef.current
      )
    },
    []
  )

  const handleConfirm = useCallback(() => {
    if (selected && selectedWorkspaceId) {
      const reference: DeploymentReference = {
        deployment_id: selected.deploymentId,
        workspace_id: selectedWorkspaceId,
        deployment_name: selected.name,
        public_url: selected.publicUrl,
        embedded_view_url: selected.embedded_view_url,
        internal_url: selected.service_name ? `http://${selected.service_name}` : selected.publicUrl,
      }
      onConfirm(reference)
    }
    onOpenChange(false)
  }, [selected, selectedWorkspaceId, onConfirm, onOpenChange])

  const handleClear = useCallback(() => {
    onConfirm(null)
    onOpenChange(false)
  }, [onConfirm, onOpenChange])

  const renderNode = useCallback(
    (node: TreeNode, path: string[], depth: number = 0) => {
      const isDeployment = node.type === "deployment"
      const deploymentData = isDeployment ? (node.data as DeploymentInfo) : null
      const isSelected = isDeployment && selected?.deploymentId === node.id
      // Check if this specific item is the auto-detected/default one
      const isFallbackItem = isDeployment && fallbackDeploymentIdRef.current === node.id
      // Check if this is the previously saved (non-fallback) selection
      const isSavedItem = isDeployment && !isFallbackItem &&
        savedSelectionRef.current !== null &&
        node.id === savedSelectionRef.current.deploymentId
      // Use blue for auto-detected/default, green for manually selected
      const selectedColor = isSelected && selectionIsFallback ? "blue" : "green"

      // Filter by search query
      if (searchQuery && isDeployment) {
        const query = searchQuery.toLowerCase()
        if (!node.name.toLowerCase().includes(query)) {
          return null
        }
      }

      const currentPath = [...path, node.id]

      return (
        <div key={node.id}>
          <div
            ref={isSelected ? selectedItemRef : undefined}
            className={cn(
              "flex items-center gap-2 py-1.5 px-2 rounded-md cursor-pointer hover:bg-muted/50",
              isSelected && selectedColor === "blue"
                ? "bg-blue-50 hover:bg-blue-100 dark:bg-blue-900/20 dark:hover:bg-blue-900/30"
                : isSelected && "bg-green-50 hover:bg-green-100 dark:bg-green-900/20 dark:hover:bg-green-900/30"
            )}
            style={{ paddingLeft: `${depth * 16 + 8}px` }}
            onClick={() => {
              if (isDeployment && node.workspaceId) {
                handleSelectDeployment(deploymentData!, node.workspaceId)
              } else {
                toggleNode(node.id, currentPath)
              }
            }}
          >
            {/* Expand/Collapse Icon */}
            {!isDeployment && (
              <button
                className="p-0.5 hover:bg-muted rounded"
                onClick={(e) => {
                  e.stopPropagation()
                  toggleNode(node.id, currentPath)
                }}
              >
                {node.isLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                ) : node.isExpanded ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                )}
              </button>
            )}

            {/* Icon based on type */}
            {node.type === "repository" && (
              node.isExpanded ? (
                <FolderOpen className="h-4 w-4 text-blue-500" />
              ) : (
                <Folder className="h-4 w-4 text-blue-500" />
              )
            )}
            {node.type === "workspace" && (
              node.isExpanded ? (
                <FolderOpen className="h-4 w-4 text-amber-500" />
              ) : (
                <Folder className="h-4 w-4 text-amber-500" />
              )
            )}
            {isDeployment && (
              <Rocket className={cn("h-4 w-4", isSelected
                ? selectedColor === "blue"
                  ? "text-blue-600 dark:text-blue-400"
                  : "text-green-600 dark:text-green-400"
                : "text-muted-foreground")} />
            )}

            {/* Name */}
            <span className={cn("flex-1 text-sm", isSelected && (selectedColor === "blue"
              ? "font-medium text-blue-700 dark:text-blue-400"
              : "font-medium text-green-700 dark:text-green-400"))}>
              {node.name}
            </span>

            {/* Auto-detected badge - always visible on the fallback item */}
            {isFallbackItem && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                Auto-detected
              </span>
            )}

            {/* Selected badge - visible on the saved (non-fallback) item */}
            {isSavedItem && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
                Selected
              </span>
            )}

            {/* Selection indicator */}
            {isSelected && <Check className={cn("h-4 w-4", selectedColor === "blue"
              ? "text-blue-600 dark:text-blue-400"
              : "text-green-600 dark:text-green-400")} />}

            {/* Status badge for deployments */}
            {isDeployment && deploymentData && (
              <span
                className={cn(
                  "text-xs px-1.5 py-0.5 rounded",
                  deploymentData.status === "Running"
                    ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                    : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
                )}
              >
                {deploymentData.status}
              </span>
            )}
          </div>

          {/* Render children if expanded */}
          {node.isExpanded && node.children.length > 0 && (
            <div>
              {node.children.map((child) => renderNode(child, currentPath, depth + 1))}
            </div>
          )}
        </div>
      )
    },
    [searchQuery, selected, selectionIsFallback, toggleNode, handleSelectDeployment]
  )

  // Filter nodes based on search
  const filteredNodes = searchQuery
    ? nodes.filter((node) => {
        return true
      })
    : nodes

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl h-[70vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search deployments..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>

        {/* Tree View */}
        <div className="flex-1 overflow-auto border rounded-md p-2">
          {loading ? (
            <div className="space-y-2 p-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-full text-destructive">
              <p>{error}</p>
            </div>
          ) : filteredNodes.length === 0 ? (
            <div className="flex items-center justify-center h-full text-muted-foreground">
              <p>No projects found</p>
            </div>
          ) : (
            <div className="space-y-0.5">
              {filteredNodes.map((node) => renderNode(node, [], 0))}
            </div>
          )}
        </div>

        {/* Selected deployment info */}
        {selected && (
          <div className="border rounded-md p-3 bg-muted/30">
            <div className="flex items-center gap-2">
              <Rocket className={cn("h-4 w-4", selectionIsFallback
                ? "text-blue-600 dark:text-blue-400"
                : "text-green-600 dark:text-green-400")} />
              <span className="font-medium">{selected.name}</span>
              {selectionIsFallback && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                  Auto-detected
                </span>
              )}
            </div>
            {selected.publicUrl && (
              <p className="text-xs text-muted-foreground mt-1 truncate">
                {selected.publicUrl}
              </p>
            )}
          </div>
        )}

        <DialogFooter className="flex items-center justify-between">
          <div className="flex gap-2">
            {selectedDeployment && (
              <Button variant="outline" onClick={handleClear}>
                Clear Selection
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={handleConfirm} disabled={!selected}>
              Confirm
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
