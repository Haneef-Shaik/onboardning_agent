import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MASK, MaskedField, maskedText } from "./masked-field";

// Synthetic values only: a made-up PAN in the vault's masked form and a made-up token.
const MASKED_PAN = "XXXXXX123X";
const TOKEN = "ptk_pan_0f3c9a1e";
const REVEALED_PAN = "ABCDE1234F";

afterEach(cleanup);

describe("maskedText", () => {
  it("shows the server's masked form as is", () => {
    expect(maskedText(MASKED_PAN)).toBe(MASKED_PAN);
  });

  it("never shows a vault token: a ptk_ value becomes the mask", () => {
    expect(maskedText(TOKEN)).toBe(MASK);
    expect(maskedText("  ptk_x  ")).toBe(MASK);
  });

  it("shows a dash when there is no value", () => {
    expect(maskedText(null)).toBe("—");
    expect(maskedText(undefined)).toBe("—");
    expect(maskedText("")).toBe("—");
  });
});

describe("MaskedField", () => {
  it("renders masked by default, with its label", () => {
    render(<MaskedField label="PAN" masked={MASKED_PAN} />);

    const field = screen.getByTestId("masked-field");
    expect(field).toHaveAttribute("data-state", "masked");
    expect(screen.getByText("PAN")).toBeInTheDocument();
    expect(screen.getByText(MASKED_PAN)).toBeInTheDocument();
  });

  it("never renders a token, even when one is passed as the masked form", () => {
    const { container } = render(<MaskedField label="Aadhaar" masked={TOKEN} />);

    expect(container.textContent).not.toContain("ptk_");
    expect(screen.getByText(MASK)).toBeInTheDocument();
  });

  it("shows no reveal control unless the caller passes onReveal", () => {
    render(<MaskedField label="PAN" masked={MASKED_PAN} />);

    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers a reveal control when allowed, and calls onReveal", async () => {
    const onReveal = vi.fn();
    render(<MaskedField label="PAN" masked={MASKED_PAN} onReveal={onReveal} />);

    await userEvent.click(screen.getByRole("button", { name: "Reveal PAN" }));

    expect(onReveal).toHaveBeenCalledOnce();
  });

  it("shows the plain value only once revealed, with a hide control", async () => {
    const onHide = vi.fn();
    render(<MaskedField label="PAN" masked={MASKED_PAN} revealed={REVEALED_PAN} onHide={onHide} />);

    expect(screen.getByTestId("masked-field")).toHaveAttribute("data-state", "revealed");
    expect(screen.getByText(REVEALED_PAN)).toBeInTheDocument();
    expect(screen.queryByText(MASKED_PAN)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Hide PAN" }));
    expect(onHide).toHaveBeenCalledOnce();
  });

  it("treats an empty revealed value as not revealed", () => {
    render(<MaskedField label="PAN" masked={MASKED_PAN} revealed="" />);

    expect(screen.getByTestId("masked-field")).toHaveAttribute("data-state", "masked");
  });
});
