import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ReactNode } from "react";

interface DataItem {
  label: string;
  value: ReactNode;
  className?: string;
  valueClassName?: string;
}

interface DataCardProps {
  title: string;
  items: DataItem[];
  headerAction?: ReactNode;
}

export function DataCard({ title, items, headerAction }: DataCardProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{title}</CardTitle>
          {headerAction && <div>{headerAction}</div>}
        </div>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {items.map((item, index) => (
            <div
              key={index}
              className={`flex flex-col space-y-1 ${item.className ?? ""}`}
            >
              <dt className="text-sm font-medium text-muted-foreground">
                {item.label}
              </dt>
              <dd className={`text-sm ${item.valueClassName ?? ""}`}>
                {item.value || "-"}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
