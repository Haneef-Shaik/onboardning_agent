import { Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "@onb/ts-ui";
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthProvider";
import { ErrorNotice } from "../components/ErrorNotice";

export function LoginPage() {
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const signIn = useMutation({ mutationFn: () => auth.login(email.trim(), password) });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    signIn.mutate();
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <p className="text-sm text-slate-500">Onboarding platform</p>
        </CardHeader>
        <CardContent>
          {auth.notice && (
            <p role="status" className="mb-4 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
              {auth.notice}
            </p>
          )}
          <form className="flex flex-col gap-4" onSubmit={submit}>
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" autoComplete="username" required maxLength={320} value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input id="password" type="password" autoComplete="current-password" required maxLength={256} value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            {signIn.isError && <ErrorNotice error={signIn.error} context="login" />}
            <Button type="submit" disabled={signIn.isPending}>
              {signIn.isPending ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
