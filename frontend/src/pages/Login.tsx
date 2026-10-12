import { ArrowLeft, KeyRound } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { IS_MOCK } from '../components/AppShell';
import { Wordmark } from '../components/Logo';
import { Button, ErrorNote, Field, Input } from '../components/ui';
import { api, ApiError } from '../lib/api';

export default function Login() {
  const { user, setUser } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const from = (loc.state as { from?: string } | null)?.from || '/app';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [step, setStep] = useState<'password' | 'totp'>('password');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (user) return <Navigate to={from} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const res = await api.auth.login(email.trim(), password, step === 'totp' ? totp.trim() : undefined);
      const u = res?.user ?? (await api.auth.me());
      setUser(u);
      nav(from, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === 'totp_required') {
        setStep('totp');
      } else if (err instanceof ApiError && err.code === 'invalid_totp') {
        setError('That code didn’t work. Check your authenticator app and try again.');
      } else if (err instanceof ApiError && err.status === 401) {
        setError('Email or password is incorrect.');
      } else if (err instanceof ApiError && err.status === 429) {
        setError('Too many attempts. Wait a minute and try again.');
      } else {
        setError(err instanceof Error ? err.message : 'Sign-in failed');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen place-items-center bg-ink-950 bg-[radial-gradient(ellipse_at_top,rgba(154,118,255,0.12),transparent_55%)] px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex justify-center"><Link to="/" aria-label="OFM Engine home"><Wordmark /></Link></div>
        <form onSubmit={submit} className="card space-y-4 p-6" aria-labelledby="login-title" noValidate>
          <div>
            <h1 id="login-title" className="text-lg font-semibold text-white">{step === 'password' ? 'Sign in to your agency' : 'Two-factor verification'}</h1>
            <p className="mt-1 text-sm text-slate-400">{step === 'password' ? 'Use your work email and password.' : 'Enter the 6-digit code from your authenticator app.'}</p>
          </div>
          {error && <ErrorNote error={error} />}
          {step === 'password' ? (
            <>
              <Field label="Email">{(id) => <Input id={id} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
              <Field label="Password">{(id) => <Input id={id} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}</Field>
            </>
          ) : (
            <Field label="Authentication code">
              {(id) => <Input id={id} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={6} autoFocus required value={totp}
                onChange={(e) => setTotp(e.target.value.replace(/\D/g, ''))} className="text-center font-mono text-lg tracking-[0.5em]" />}
            </Field>
          )}
          <Button type="submit" loading={busy} className="w-full" disabled={step === 'password' ? !email || !password : totp.length < 6}>
            {step === 'password' ? 'Sign in' : <><KeyRound className="h-4 w-4" aria-hidden /> Verify</>}
          </Button>
          {step === 'totp' && (
            <button type="button" onClick={() => { setStep('password'); setTotp(''); setError(null); }} className="flex items-center gap-1 text-xs text-slate-400 hover:text-white">
              <ArrowLeft className="h-3 w-3" aria-hidden /> Use a different account
            </button>
          )}
        </form>
        {IS_MOCK && (
          <p className="mt-4 rounded-lg border border-amber-400/20 bg-amber-400/5 p-3 text-xs text-amber-100/80">
            Demo mode: <b>admin@demo.agency</b> or <b>member@demo.agency</b> (password <b>demo1234</b>). <b>owner@demo.agency</b> has 2FA — code <b>123456</b>.
          </p>
        )}
      </div>
    </div>
  );
}
