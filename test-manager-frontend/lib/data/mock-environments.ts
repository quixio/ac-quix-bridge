/**
 * Mock data for Environments placeholder page
 * This is temporary data for demo purposes until the feature is fully implemented
 */

export interface MockEnvironment {
  environment_id: string
  name: string
  location: string
  status: "Active" | "Maintenance" | "Inactive"
  capacity: number
  created_at: string
}

export const mockEnvironments: MockEnvironment[] = [
  {
    environment_id: "ENV-001",
    name: "Test Bench A",
    location: "Lab 1",
    status: "Active",
    capacity: 5,
    created_at: "2024-01-15T10:30:00Z",
  },
  {
    environment_id: "ENV-002",
    name: "Test Bench B",
    location: "Lab 2",
    status: "Maintenance",
    capacity: 3,
    created_at: "2024-02-20T14:45:00Z",
  },
  {
    environment_id: "ENV-003",
    name: "Test Bench C",
    location: "Lab 1",
    status: "Inactive",
    capacity: 8,
    created_at: "2024-03-10T09:15:00Z",
  },
  {
    environment_id: "ENV-004",
    name: "Test Bench D",
    location: "Lab 3",
    status: "Active",
    capacity: 6,
    created_at: "2024-04-05T11:20:00Z",
  },
  {
    environment_id: "ENV-005",
    name: "Test Bench E",
    location: "Lab 2",
    status: "Active",
    capacity: 4,
    created_at: "2024-05-12T08:00:00Z",
  },
  {
    environment_id: "ENV-006",
    name: "Test Bench F",
    location: "Lab 4",
    status: "Maintenance",
    capacity: 7,
    created_at: "2024-06-18T15:30:00Z",
  },
  {
    environment_id: "ENV-007",
    name: "Test Bench G",
    location: "Lab 3",
    status: "Active",
    capacity: 10,
    created_at: "2024-07-22T13:45:00Z",
  },
]
