/**
 * CSV utility functions for converting JSON data to CSV format
 */

/**
 * Convert JSON array to CSV string
 * @param data Array of objects to convert to CSV
 * @returns CSV string with headers
 */
export function jsonToCsv(data: any[]): string {
  if (!data || data.length === 0) {
    return ""
  }

  // Get headers from first object keys
  const headers = Object.keys(data[0])

  // Escape CSV value (handle quotes, commas, newlines)
  const escapeValue = (value: any): string => {
    if (value === null || value === undefined) {
      return ""
    }

    const stringValue = String(value)

    // If value contains comma, quote, or newline, wrap in quotes and escape existing quotes
    if (stringValue.includes(",") || stringValue.includes('"') || stringValue.includes("\n")) {
      return `"${stringValue.replace(/"/g, '""')}"`
    }

    return stringValue
  }

  // Build CSV rows
  const rows: string[] = []

  // Add header row
  rows.push(headers.map(escapeValue).join(","))

  // Add data rows
  for (const item of data) {
    const row = headers.map((header) => escapeValue(item[header]))
    rows.push(row.join(","))
  }

  return rows.join("\n")
}

/**
 * Trigger browser download of CSV data
 * @param csvContent CSV string content
 * @param filename Filename for the download
 */
export function downloadCsv(csvContent: string, filename: string): void {
  // Create blob from CSV content
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" })

  // Create object URL
  const url = URL.createObjectURL(blob)

  // Create temporary link element
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  link.style.display = "none"

  // Append to body, click, and cleanup
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)

  // Revoke object URL to free memory
  URL.revokeObjectURL(url)
}
