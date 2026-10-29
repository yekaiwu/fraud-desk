import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginForm } from "./LoginForm";

afterEach(() => vi.unstubAllGlobals());

const reply = (status: number, body: unknown) => vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));

describe("LoginForm", () => {
  it("shows the one-sentence footer", () => {
    render(<LoginForm />);
    expect(screen.getByText(/Demo with fictitious data only/)).toBeInTheDocument();
  });

  it("links to the public product tour", () => {
    render(<LoginForm />);
    expect(screen.getByRole("link", { name: "How it works" })).toHaveAttribute("href", "/welcome");
  });

  it("posts the username and password and shows a wrong-credentials message", async () => {
    const f = reply(401, { error: { code: "invalid_credentials", message: "Wrong username or password." } });
    vi.stubGlobal("fetch", f);
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Username"), "officer");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong username or password.");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/auth/login");
    expect(JSON.parse(init.body)).toMatchObject({ username: "officer", password: "nope" });
  });

  it("shows the lock-out message on 429", async () => {
    vi.stubGlobal("fetch", reply(429, { error: { code: "rate_limited", message: "Too many failed attempts. Try again in 10 minute(s)." } }));
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Username"), "officer");
    await userEvent.type(screen.getByLabelText("Password"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Too many failed attempts"));
  });

  it("can show and hide the password you typed", async () => {
    render(<LoginForm />);
    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("type", "password");
    await userEvent.type(input, "secret");
    await userEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(input).toHaveAttribute("type", "text");
    expect(input).toHaveValue("secret");
    await userEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(input).toHaveAttribute("type", "password");
  });

  it("keeps Sign in disabled until both a username and a password are typed", async () => {
    render(<LoginForm />);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Password"), "x");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Username"), "officer");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });
});
