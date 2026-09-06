import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api"
import { Activity, Loader2, Lock, Mail } from "lucide-react"

/**
 * The access-key prompt.
 *
 * Renders in place of the app until the server says this browser is through the
 * gate. The key is exchanged for an HttpOnly cookie and never held in React
 * state or storage — a token this code could read is a token an injected script
 * could read too.
 *
 * When no key is configured server-side the gate reports itself as satisfied
 * and this never appears, which is what keeps local development unobstructed.
 */
export function AccessGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false)
  const [allowed, setAllowed] = useState(false)
  const [email, setEmail] = useState("")
  const [key, setKey] = useState("")
  const [mailbox, setMailbox] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    api.authStatus()
      .then((s) => { setAllowed(s.authenticated); setMailbox(s.mailbox ?? null) })
      // If the check itself fails the server is unreachable, not the key — show
      // the app and let its own error handling say so.
      .catch(() => setAllowed(true))
      .finally(() => setReady(true))
  }, [])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setError("")
    try {
      await api.authenticate(email.trim(), key)
      setKey("")            // drop it as soon as the cookie exists
      setAllowed(true)
    } catch (err: any) {
      setError(err.message || "That access key is not correct.")
    } finally { setBusy(false) }
  }

  if (!ready) return null
  if (allowed) return <>{children}</>

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Activity className="size-4" />
          </div>
          <div>
            <div className="text-sm font-semibold leading-none">Dossier</div>
            <div className="text-[11px] text-muted-foreground">
              Prospect research &amp; outreach drafting
            </div>
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="login-email" className="flex items-center gap-1.5">
            <Mail className="size-3.5" /> Email
          </Label>
          <Input id="login-email" type="email" autoFocus autoComplete="username"
            value={email} onChange={(e) => setEmail(e.target.value)}
            placeholder={mailbox ?? "you@gmail.com"} />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="access-key" className="flex items-center gap-1.5">
            <Lock className="size-3.5" /> App password
          </Label>
          <Input id="access-key" type="password" autoComplete="current-password"
            value={key} onChange={(e) => setKey(e.target.value)}
            placeholder="16 characters from Google" />
          {error
            ? <p className="text-[11px] text-destructive">{error}</p>
            : <p className="text-[11px] text-muted-foreground">
                Your Gmail app password, not your account password. This is also the
                mailbox your messages will be sent from.
              </p>}
        </div>

        <Button type="submit" className="w-full" disabled={busy || !key.trim()}>
          {busy && <Loader2 className="animate-spin" />} Sign in
        </Button>

        <p className="text-center text-[11px] text-muted-foreground">
          No app password?{" "}
          <a href="https://myaccount.google.com/apppasswords" target="_blank"
            rel="noreferrer" className="underline underline-offset-2">
            Create one
          </a>{" "}— needs 2-step verification on the account.
        </p>
      </form>
    </div>
  )
}
