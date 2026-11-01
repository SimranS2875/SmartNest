import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";
import { BudgetView } from "./components/BudgetView";
import { ChatPanel } from "./components/ChatPanel";
import { ItemCard } from "./components/ItemCard";
import { inr, inrShort, percent, timeAgo } from "./format";
import type { BudgetView as Budget, PlanItem, Product } from "./types";

const product = (over: Partial<Product> = {}): Product => ({
  product_id: "P1", name: "Oak Sofa", price: 18628, retailer: "Wakefit", image_url: null, data_origin: "serpapi",
  simulated: false, buy_url: "https://www.wakefit.co/sofa", link_label: "Retailer page", url_type: "retailer",
  rating: 4.5, review_count: 12, seating_capacity: 3, retrieved_at: "2026-10-31T00:00:00Z", availability: "in_stock", ...over,
});

const item = (over: Partial<PlanItem> = {}): PlanItem => ({
  slot_id: "living_room.sofa", room_id: "living_room", room: "Living room", category: "sofa", category_label: "Sofa",
  label: "Living room sofa", ceiling: 32000, status: "confirmed", product: product(), utility: 58.8, completeness: 0.43,
  unavailable: ["style"], contributions: { price: 22.8, quality: 36 }, alternatives: [], why: "Highest utility.",
  inspected: 10, dropped_accessories: 0, data_origin: "serpapi", ...over,
});

const noop = {
  onShortlist: vi.fn(), onChoose: vi.fn(), onClearChoice: vi.fn(), onOutOfStock: vi.fn(), onRemove: vi.fn(),
};

afterEach(() => { vi.restoreAllMocks(); vi.clearAllMocks(); });

describe("format", () => {
  it("groups rupees the Indian way", () => {
    expect([0, 999, 1000, 200000, 1234567, -5000].map(inr)).toEqual(["₹0", "₹999", "₹1,000", "₹2,00,000", "₹12,34,567", "-₹5,000"]);
    expect(inr(null)).toBe("—");
    expect(inr(NaN)).toBe("—");
  });
  it("shortens axis values", () => {
    expect([500, 45000, 200000, 25000000].map(inrShort)).toEqual(["₹500", "₹45k", "₹2L", "₹2.5Cr"]);
  });
  it("formats percentages and ages", () => {
    expect(percent(0.43)).toBe("43%");
    expect(percent(null)).toBe("—");
    const now = new Date("2026-11-01T12:00:00Z");
    expect(timeAgo("2026-11-01T11:30:00Z", now)).toBe("under an hour ago");
    expect(timeAgo("2026-11-01T00:00:00Z", now)).toBe("12 h ago");
    expect(timeAgo("2026-10-20T12:00:00Z", now)).toBe("12 days ago");
    expect(timeAgo("garbage", now)).toBe("unknown");
    expect(timeAgo(null, now)).toBe("unknown");
  });
});

describe("ItemCard", () => {
  it("shows a confirmed real listing with a safe external link", () => {
    render(<ItemCard item={item()} shortlist={[]} busy={false} {...noop} />);
    expect(screen.getByText("Confirmed in stock")).toBeInTheDocument();
    expect(screen.getByText("Real listing")).toBeInTheDocument();
    expect(screen.getByText("₹18,628")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Retailer page/ });
    expect(link).toHaveAttribute("href", "https://www.wakefit.co/sofa");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("labels unverified stock and never calls it confirmed", () => {
    render(<ItemCard item={item({ status: "unverified" })} shortlist={[]} busy={false} {...noop} />);
    expect(screen.getByText("Stock unverified")).toBeInTheDocument();
    expect(screen.queryByText("Confirmed in stock")).not.toBeInTheDocument();
  });

  it("gives simulated products no purchase link and a sample badge", () => {
    const sim = product({ simulated: true, data_origin: "simulated", buy_url: null, link_label: "Sample listing - not a real product" });
    render(<ItemCard item={item({ product: sim })} shortlist={[]} busy={false} {...noop} />);
    expect(screen.getByText("Sample data")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText(/not a real product/)).toBeInTheDocument();
  });

  it("never renders a link for a simulated product even if a URL leaks in", () => {
    const sim = product({ simulated: true, buy_url: "https://evil.example" });
    render(<ItemCard item={item({ product: sim })} shortlist={[]} busy={false} {...noop} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("explains an unfilled item instead of pretending", () => {
    render(<ItemCard item={item({ product: null, status: "unfilled", why: "No sofa fits; nothing was relaxed." })}
      shortlist={[]} busy={false} {...noop} />);
    expect(screen.getByText("Not filled")).toBeInTheDocument();
    expect(screen.getByText(/nothing was relaxed/)).toBeInTheDocument();
  });

  it("wires the actions", async () => {
    const user = userEvent.setup();
    const alt = { ...product({ product_id: "P2", name: "Pine Sofa", price: 20000 }), state: "unverified" as const, utility: 50, completeness: 0.4, gaps: [] };
    render(<ItemCard item={item({ alternatives: [alt] })} shortlist={[]} busy={false} {...noop} />);
    await user.click(screen.getByRole("button", { name: "Add to shortlist" }));
    expect(noop.onShortlist).toHaveBeenCalledWith("P1");
    await user.click(screen.getByRole("button", { name: /Alternatives \(1\)/ }));
    await user.click(screen.getByRole("button", { name: "Use Pine Sofa" }));
    expect(noop.onChoose).toHaveBeenCalledWith("living_room.sofa", "P2");
    await user.click(screen.getByRole("button", { name: "Why this?" }));
    await user.click(screen.getByRole("button", { name: "Mark out of stock" }));
    expect(noop.onOutOfStock).toHaveBeenCalledWith("P1");
    await user.click(screen.getByRole("button", { name: "Remove item" }));
    expect(noop.onRemove).toHaveBeenCalledWith("living_room.sofa");
  });

  it("disables destructive actions while busy", async () => {
    const user = userEvent.setup();
    render(<ItemCard item={item()} shortlist={[]} busy {...noop} />);
    await user.click(screen.getByRole("button", { name: "Why this?" }));
    expect(screen.getByRole("button", { name: "Mark out of stock" })).toBeDisabled();
  });

  it("shows the score as an accessible meter", () => {
    render(<ItemCard item={item()} shortlist={[]} busy={false} {...noop} />);
    expect(screen.getByRole("meter", { name: /Utility score/ })).toHaveAttribute("aria-valuenow", "59");
  });

  it("survives a broken image", () => {
    render(<ItemCard item={item({ product: product({ image_url: "https://x.example/a.jpg" }) })} shortlist={[]} busy={false} {...noop} />);
    screen.getByRole("img", { name: "Oak Sofa" }).dispatchEvent(new Event("error"));
    return waitFor(() => expect(screen.queryByRole("img", { name: "Oak Sofa" })).not.toBeInTheDocument());
  });
});

describe("ChatPanel", () => {
  it("sends trimmed text and clears the box", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatPanel history={[]} hasPlan={false} busy={false} onSend={onSend} />);
    await user.type(screen.getByLabelText("Message SmartNest"), "  2BHK 2 lakh  {enter}");
    expect(onSend).toHaveBeenCalledWith("2BHK 2 lakh");
    expect(screen.getByLabelText("Message SmartNest")).toHaveValue("");
  });

  it("ignores blank input and is locked while busy", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    const { rerender } = render(<ChatPanel history={[]} hasPlan={false} busy={false} onSend={onSend} />);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.type(screen.getByLabelText("Message SmartNest"), "   {enter}");
    expect(onSend).not.toHaveBeenCalled();
    rerender(<ChatPanel history={[]} hasPlan={false} busy onSend={onSend} />);
    expect(screen.getByRole("status")).toHaveTextContent("thinking");
  });

  it("offers the example before a plan and follow-ups after", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    const { rerender } = render(<ChatPanel history={[]} hasPlan={false} busy={false} onSend={onSend} />);
    await user.click(screen.getByRole("button", { name: "Try the 2BHK example" }));
    expect(onSend.mock.calls[0][0]).toContain("2BHK");
    rerender(<ChatPanel history={[]} hasPlan busy={false} onSend={onSend} />);
    expect(screen.getByRole("button", { name: "Why this sofa?" })).toBeInTheDocument();
  });

  it("renders both roles and error replies", () => {
    render(<ChatPanel hasPlan busy={false} onSend={vi.fn()} history={[
      { role: "user", text: "hello", kind: "", time: "" }, { role: "assistant", text: "nope", kind: "error", time: "" }]} />);
    expect(screen.getByText("hello")).toBeInTheDocument();
    expect(screen.getByText("nope")).toHaveClass("text-bad");
  });
});

describe("BudgetView", () => {
  const budget: Budget = {
    total: 200000, spent: 100000, remaining: 100000, utilisation: 0.5, within_budget: true,
    by_room: [{ name: "Living room", value: 100000 }], by_category: [{ name: "Sofa", value: 100000 }],
    items: [{ label: "Living room sofa", room: "Living room", category: "Sofa", ceiling: 32000, price: 18628, status: "unverified" }],
    allocation_vs_spend: [{ room: "Living room", allocated: 32000, spent: 18628 }],
    counts: { confirmed: 0, unverified: 1, unfilled: 0 },
  };

  it("shows the headline numbers and the item table", async () => {
    render(<BudgetView budget={budget} />);
    expect(screen.getByText("₹2,00,000")).toBeInTheDocument();
    expect(screen.getAllByText("₹1,00,000").length).toBe(2);
    expect(screen.getByRole("meter", { name: "Budget used" })).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByRole("cell", { name: "Living room sofa" })).toBeInTheDocument();
  });

  it("says so when there is no plan", () => {
    render(<BudgetView budget={null} />);
    expect(screen.getByText("No budget yet")).toBeInTheDocument();
  });
});

describe("api client", () => {
  const respond = (status: number, body: unknown) =>
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

  it("returns parsed JSON", async () => {
    respond(200, { status: "ok" });
    expect((await api.health()).status).toBe("ok");
  });

  it("surfaces the server's message", async () => {
    respond(422, { detail: "that product cannot be bought" });
    await expect(api.event("s", { type: "remove_item", slot_id: "x" })).rejects.toMatchObject({ status: 422, message: "that product cannot be bought" });
  });

  it("reads FastAPI validation errors", async () => {
    respond(422, { detail: [{ msg: "Input should be greater than 0" }] });
    await expect(api.createSession({ total_budget: -1 })).rejects.toThrow("Input should be greater than 0");
  });

  it("gives a generic message for non-JSON failures", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("<html>", { status: 502 }));
    await expect(api.health()).rejects.toThrow("Request failed (502)");
  });

  it("reports an unreachable server", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    const err = await api.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toMatch(/Cannot reach/);
  });

  it("encodes ids in paths", async () => {
    const spy = respond(200, {});
    await api.getSession("a/b c");
    expect(spy.mock.calls[0][0]).toBe("/api/sessions/a%2Fb%20c");
  });
});
