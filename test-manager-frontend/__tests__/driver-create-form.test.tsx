import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DriverCreateForm } from "@/components/drivers/driver-create-form";

const createMock = vi.fn();
const toastMock = vi.fn();

vi.mock("@/lib/hooks/use-api", () => ({
  useDriversApi: () => ({ create: createMock }),
}));
vi.mock("@/lib/hooks/use-toast", () => ({
  useToast: () => ({ toast: toastMock }),
}));

describe("DriverCreateForm", () => {
  beforeEach(() => {
    createMock.mockReset();
    toastMock.mockReset();
  });

  it("disables submit until name, email, and company are filled", () => {
    render(<DriverCreateForm onCreated={vi.fn()} />);
    const submit = screen.getByRole("button", { name: /create driver/i });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: "New Guy" },
    });
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "new@guy.com" },
    });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/company/i), {
      target: { value: "ACME" },
    });
    expect(submit).toBeEnabled();
  });

  it("trims fields, creates the driver, and calls onCreated with the result", async () => {
    const created = {
      driver_id: "DRV-9",
      name: "New Guy",
      email: "new@guy.com",
      company: "ACME",
      created_at: "",
      updated_at: "",
    };
    createMock.mockResolvedValue(created);
    const onCreated = vi.fn();
    render(<DriverCreateForm onCreated={onCreated} />);

    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: "  New Guy  " },
    });
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "new@guy.com" },
    });
    fireEvent.change(screen.getByLabelText(/company/i), {
      target: { value: "ACME" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create driver/i }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
    expect(createMock).toHaveBeenCalledWith({
      name: "New Guy",
      email: "new@guy.com",
      company: "ACME",
    });
  });

  it("shows a destructive toast and re-enables submit on API failure", async () => {
    createMock.mockRejectedValue(new Error("Conflict"));
    const onCreated = vi.fn();
    render(<DriverCreateForm onCreated={onCreated} />);

    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: "Dup" },
    });
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "dup@x.com" },
    });
    fireEvent.change(screen.getByLabelText(/company/i), {
      target: { value: "ACME" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create driver/i }));

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ variant: "destructive" }),
      ),
    );
    expect(onCreated).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: /create driver/i }),
    ).toBeEnabled();
  });

  it("renders a Cancel button only when onCancel is provided", () => {
    const { rerender } = render(<DriverCreateForm onCreated={vi.fn()} />);
    expect(
      screen.queryByRole("button", { name: /cancel/i }),
    ).not.toBeInTheDocument();

    rerender(<DriverCreateForm onCreated={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
  });
});
