"use client"

import { useState, useEffect } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { useToast } from "@/lib/hooks/use-toast"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { apiGet, apiPost } from "@/lib/api/client"

const RIG_OPTIONS = [
  { value: "XPS", label: "XPS" },
  { value: "patrickpc", label: "patrickpc" },
]

const ENVIRONMENT_OPTIONS = [
  { value: "prague_office", label: "Prague Office" },
  { value: "patricks_office", label: "Patrick's Office" },
]

const TEST_RIG_OPTIONS = [
  { value: "g29", label: "Logitech G29" },
  { value: "g923", label: "Logitech G923" },
  { value: "dd_pro", label: "Fanatec DD Pro" },
  { value: "simucube", label: "Simucube" },
]

const DRIVER_OPTIONS = [
  "Ludvik", "Peter", "Mike", "Tomas", "Steve", "Luis", "Chris",
  "Richard", "Javi", "Patrick", "Daniel", "Lajos", "Merlin",
  "Quique", "Emanuel", "Matt", "Ricki",
]

interface CurrentConfigs {
  [targetKey: string]: {
    test_id?: string
    [key: string]: any
  }
}

export default function ExperimentConfigPage() {
  const { toast } = useToast()
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [currentConfigs, setCurrentConfigs] = useState<CurrentConfigs>({})

  const [rigHostname, setRigHostname] = useState("XPS")
  const [environment, setEnvironment] = useState("prague_office")
  const [testRig, setTestRig] = useState("g29")
  const [experimentId, setExperimentId] = useState("")
  const [driver, setDriver] = useState("ludvik")
  const [requirements, setRequirements] = useState("")

  const loadCurrentConfigs = async () => {
    try {
      const data = await apiGet("/api/v1/experiment-config/current")
      setCurrentConfigs(data.configs || {})
    } catch {
      // Silently fail — configs display is not critical
    }
  }

  useEffect(() => {
    loadCurrentConfigs()
  }, [])

  const handleSubmit = async () => {
    if (!experimentId.trim()) {
      toast({
        title: "Validation error",
        description: "Please fill in the Experiment ID.",
        variant: "destructive",
      })
      return
    }

    setIsSubmitting(true)
    try {
      const result = await apiPost("/api/v1/experiment-config/submit", {
        rig_hostname: rigHostname,
        environment,
        test_rig: testRig,
        experiment_id: experimentId,
        driver,
        requirements,
      })

      toast({
        title: "Config submitted",
        description: `${result.target_key}: ${result.test_id}`,
      })

      loadCurrentConfigs()
    } catch (error) {
      toast({
        title: "Error submitting config",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  const currentConfigSummary = Object.entries(currentConfigs)
    .map(([key, cfg]) => `${key}: ${cfg.test_id || "no test_id"}`)
    .join(" | ")

  return (
    <MainLayout>
      <div className="max-w-2xl mx-auto space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Experiment Config</h1>
          <p className="text-muted-foreground mt-1">
            {currentConfigSummary || "No active configs"}
          </p>
        </div>

        {/* Setup */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wider text-muted-foreground">Setup</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label htmlFor="rig">Rig (Hostname)</Label>
              <Select value={rigHostname} onValueChange={setRigHostname}>
                <SelectTrigger id="rig">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RIG_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="environment">Environment</Label>
                <Select value={environment} onValueChange={setEnvironment}>
                  <SelectTrigger id="environment">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ENVIRONMENT_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor="testRig">Test Rig</Label>
                <Select value={testRig} onValueChange={setTestRig}>
                  <SelectTrigger id="testRig">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TEST_RIG_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label htmlFor="experimentId">Experiment ID</Label>
              <Input
                id="experimentId"
                placeholder="e.g. tyre_pressure_comparison"
                value={experimentId}
                onChange={(e) => setExperimentId(e.target.value)}
              />
            </div>
          </CardContent>
        </Card>

        {/* Session */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wider text-muted-foreground">Session</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label htmlFor="driver">Driver</Label>
              <Select value={driver} onValueChange={setDriver}>
                <SelectTrigger id="driver">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DRIVER_OPTIONS.map((name) => (
                    <SelectItem key={name.toLowerCase()} value={name.toLowerCase()}>{name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="requirements">Requirements</Label>
              <Textarea
                id="requirements"
                placeholder="e.g. The system shall complete a lap in under 2 minutes. Tyre temperature shall not exceed 100&#176;C."
                value={requirements}
                onChange={(e) => setRequirements(e.target.value)}
                rows={3}
              />
            </div>
          </CardContent>
        </Card>

        {/* Preview */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wider text-muted-foreground">Preview</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs bg-muted p-4 rounded-lg overflow-auto">
              {JSON.stringify(
                {
                  target_key: rigHostname,
                  environment,
                  test_rig: testRig,
                  experiment_id: experimentId,
                  driver,
                  requirements: requirements || undefined,
                },
                null,
                2
              )}
            </pre>
          </CardContent>
        </Card>

        <Button
          onClick={handleSubmit}
          disabled={isSubmitting || !experimentId.trim()}
          className="w-full"
          size="lg"
        >
          {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Submit Config
        </Button>
      </div>
    </MainLayout>
  )
}
