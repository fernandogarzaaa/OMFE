export function Logo({ className = 'h-7 w-7' }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#5cc2ff" /><stop offset="1" stopColor="#9a76ff" /></linearGradient></defs>
      <rect width="32" height="32" rx="8" fill="#11141c" stroke="rgba(255,255,255,.08)" />
      <path d="M8 21l5-10 4 7 2-3 4 6" fill="none" stroke="url(#lg)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
export function Wordmark() {
  return (
    <span className="flex items-center gap-2 font-semibold tracking-tight text-white">
      <Logo /> OFM <span className="gradient-text">Engine</span>
    </span>
  );
}
