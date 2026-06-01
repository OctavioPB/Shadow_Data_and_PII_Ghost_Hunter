import { useState } from 'react';
import { useAuthStore } from './store/authStore';
import { useLogin } from './hooks/useAuth';
import { useDemoSeed } from './hooks/useDemo';
import { useRisks, useStatsSummary, type RiskFilters } from './hooks/useRiskInventory';
import { usePIIReport, useRemediate, type RemediateRequest } from './hooks/usePIIReport';
import { useAuditLog, exportAuditLog, type AuditFilters } from './hooks/useAuditLog';
import { useDataSources } from './hooks/useDataSources';

// ─── CSS variables + global reset ────────────────────────────────────────────

const CSS_VARS = `
  @keyframes shimmer {
    0%   { background-position: -200% 0; }
    100% { background-position:  200% 0; }
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  :root {
    --primary:    #003366;
    --primary-80: #1A4D80;
    --primary-60: #336699;
    --primary-30: #99BBDD;
    --primary-10: #E0EAF4;
    --gold:       #C8982A;
    --gold-light: #E8C46A;
    --dark:       #1C1C2E;
    --mid:        #6B7280;
    --light:      #F4F6F9;
    --white:      #FFFFFF;
    --fd: 'Fraunces', Georgia, serif;
    --fb: 'Plus Jakarta Sans', sans-serif;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #F4F6F9; font-family: 'Plus Jakarta Sans', sans-serif; }
  input, select, button { font-family: 'Plus Jakarta Sans', sans-serif; }
  nav button {
    -webkit-appearance: none;
    appearance: none;
    background-color: transparent;
  }
  nav button:hover, nav button:focus, nav button:active, nav button:focus-visible {
    outline: none;
    background-color: transparent;
    -webkit-tap-highlight-color: transparent;
  }
`;

type Page = 'dashboard' | 'pii-report' | 'audit' | 'data-sources' | 'info' | 'compliance';

// ─── Shared components ────────────────────────────────────────────────────────

function Eyebrow({ children, light = false }: { children: React.ReactNode; light?: boolean }) {
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 9,
        fontFamily: 'var(--fb)',
        fontWeight: 500,
        letterSpacing: '4px',
        textTransform: 'uppercase',
        color: light ? 'var(--gold-light)' : 'var(--gold)',
        marginBottom: 10,
      }}
    >
      <div
        style={{
          width: 24,
          height: 1,
          flexShrink: 0,
          backgroundColor: light ? 'var(--gold-light)' : 'var(--gold)',
        }}
      />
      {children}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { bg: string; color: string; dot: string; label: string }> = {
    flagged:    { bg: '#FDEAEA', color: '#7A1020', dot: '#E03448', label: 'Flagged' },
    quarantined:{ bg: '#FEF0E6', color: '#7A3800', dot: '#F07020', label: 'Quarantined' },
    remediated: { bg: '#E0F7EF', color: '#0D5C3A', dot: '#27B97C', label: 'Remediated' },
    classified: { bg: '#E0EAF4', color: '#001F4D', dot: '#003366', label: 'Classified' },
    clean:      { bg: '#E0F7EF', color: '#0D5C3A', dot: '#27B97C', label: 'Clean' },
  };
  const s = map[status] ?? map['classified'];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: s.bg,
        color: s.color,
        borderRadius: 20,
        padding: '4px 12px',
        fontSize: 10,
        fontFamily: 'var(--fb)',
        fontWeight: 500,
        whiteSpace: 'nowrap',
      }}
    >
      <span
        style={{ width: 6, height: 6, borderRadius: '50%', background: s.dot, flexShrink: 0 }}
      />
      {s.label}
    </span>
  );
}

function KpiCard({ value, label, sub }: { value: string | number; label: string; sub?: string }) {
  return (
    <div
      style={{
        background: '#fff',
        borderRadius: 12,
        boxShadow: '0 1px 4px rgba(0,51,102,0.08)',
        display: 'flex',
        alignItems: 'stretch',
        overflow: 'hidden',
      }}
    >
      <div style={{ width: 3, background: 'var(--gold)', flexShrink: 0 }} />
      <div style={{ padding: '20px 24px', textAlign: 'center' }}>
        <div
          style={{
            fontFamily: "'Fraunces', Georgia, serif",
            fontSize: 32,
            fontWeight: 300,
            color: 'var(--dark)',
            lineHeight: 1,
            marginBottom: 6,
          }}
        >
          {value}
        </div>
        <div
          style={{
            fontFamily: 'var(--fb)',
            fontSize: 10,
            fontWeight: 500,
            letterSpacing: '3px',
            textTransform: 'uppercase',
            color: 'var(--mid)',
          }}
        >
          {label}
        </div>
        {sub && (
          <div style={{ fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--mid)', marginTop: 4 }}>
            {sub}
          </div>
        )}
      </div>
    </div>
  );
}

function Hero({ title, italic, sub }: { title: string; italic: string; sub: string }) {
  return (
    <div
      style={{
        backgroundColor: '#003366',
        backgroundImage: `
          linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px)
        `,
        backgroundSize: '48px 48px',
      }}
    >
      <div style={{ maxWidth: 1300, margin: '0 auto', padding: '56px 48px' }}>
        <h1
          style={{
            fontFamily: "'Fraunces', Georgia, serif",
            fontSize: 48,
            fontWeight: 300,
            color: '#fff',
            lineHeight: 1.2,
            marginBottom: 12,
          }}
        >
          {title} <em style={{ fontStyle: 'italic', color: 'var(--gold-light)' }}>{italic}</em>
        </h1>
        <p
          style={{
            fontFamily: 'var(--fb)',
            fontSize: 14,
            color: 'rgba(255,255,255,0.6)',
            lineHeight: 1.75,
            maxWidth: 580,
          }}
        >
          {sub}
        </p>
      </div>
    </div>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 95 ? '#E03448' : pct >= 85 ? '#F07020' : '#27B97C';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div
        style={{
          flex: 1,
          height: 6,
          background: 'var(--light)',
          borderRadius: 4,
          overflow: 'hidden',
        }}
      >
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4 }} />
      </div>
      <span style={{ fontFamily: 'var(--fb)', fontSize: 12, color: 'var(--mid)', minWidth: 36 }}>
        {pct}%
      </span>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <tr>
      <td
        colSpan={99}
        style={{
          padding: '56px 16px',
          textAlign: 'center',
          fontFamily: 'var(--fb)',
          fontSize: 13,
          color: 'var(--mid)',
        }}
      >
        {message}
      </td>
    </tr>
  );
}

function SkeletonRow({ cols }: { cols: number }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} style={{ padding: '12px 16px' }}>
          <div
            style={{
              height: 14,
              borderRadius: 4,
              background: 'linear-gradient(90deg, #e8edf4 25%, #f4f6f9 50%, #e8edf4 75%)',
              backgroundSize: '200% 100%',
              animation: 'shimmer 1.4s infinite',
            }}
          />
        </td>
      ))}
    </tr>
  );
}

function TableHead({ cols }: { cols: string[] }) {
  return (
    <thead>
      <tr style={{ background: 'var(--primary)' }}>
        {cols.map((c) => (
          <th
            key={c}
            style={{
              padding: '12px 16px',
              textAlign: 'left',
              fontFamily: 'var(--fb)',
              fontSize: 10,
              fontWeight: 500,
              letterSpacing: '2px',
              textTransform: 'uppercase',
              color: '#fff',
              whiteSpace: 'nowrap',
            }}
          >
            {c}
          </th>
        ))}
      </tr>
    </thead>
  );
}

function FilterInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span
        style={{
          fontFamily: 'var(--fb)',
          fontSize: 9,
          fontWeight: 600,
          letterSpacing: '2px',
          textTransform: 'uppercase',
          color: 'var(--mid)',
        }}
      >
        {label}
      </span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          padding: '6px 10px',
          borderRadius: 6,
          border: '1px solid var(--primary-10)',
          fontFamily: 'var(--fb)',
          fontSize: 13,
          color: 'var(--dark)',
          background: '#fff',
          outline: 'none',
          minWidth: 160,
        }}
      />
    </label>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span
        style={{
          fontFamily: 'var(--fb)',
          fontSize: 9,
          fontWeight: 600,
          letterSpacing: '2px',
          textTransform: 'uppercase',
          color: 'var(--mid)',
        }}
      >
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          padding: '6px 10px',
          borderRadius: 6,
          border: '1px solid var(--primary-10)',
          fontFamily: 'var(--fb)',
          fontSize: 13,
          color: 'var(--dark)',
          background: '#fff',
          outline: 'none',
          cursor: 'pointer',
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function ConfirmModal({
  open,
  title,
  message,
  onConfirm,
  onCancel,
  loading,
}: {
  open: boolean;
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
  loading?: boolean;
}) {
  if (!open) return null;
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.45)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          background: '#fff',
          borderRadius: 14,
          padding: '32px 36px',
          maxWidth: 440,
          width: '90%',
          boxShadow: '0 8px 40px rgba(0,51,102,0.18)',
        }}
      >
        <div style={{ height: 3, background: 'var(--gold)', borderRadius: 2, marginBottom: 20 }} />
        <h3
          style={{
            fontFamily: "'Fraunces', Georgia, serif",
            fontSize: 22,
            fontWeight: 300,
            color: '#0a1628',
            marginBottom: 12,
          }}
        >
          {title}
        </h3>
        <p style={{ fontFamily: 'var(--fb)', fontSize: 14, color: 'var(--mid)', lineHeight: 1.7 }}>
          {message}
        </p>
        <div style={{ display: 'flex', gap: 12, marginTop: 24, justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            style={{
              padding: '8px 20px',
              borderRadius: 6,
              border: '1px solid var(--primary-10)',
              background: '#fff',
              fontFamily: 'var(--fb)',
              fontSize: 13,
              color: 'var(--mid)',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            style={{
              padding: '8px 20px',
              borderRadius: 6,
              border: 'none',
              background: 'var(--primary)',
              fontFamily: 'var(--fb)',
              fontSize: 13,
              color: '#fff',
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.7 : 1,
            }}
          >
            {loading ? 'Processing…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Pagination({
  page,
  pages,
  total,
  onPage,
}: {
  page: number;
  pages: number;
  total: number;
  onPage: (p: number) => void;
}) {
  if (pages <= 1) return null;
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginTop: 16,
        fontFamily: 'var(--fb)',
        fontSize: 13,
        color: 'var(--mid)',
      }}
    >
      <span>{total} total results</span>
      <div style={{ display: 'flex', gap: 6 }}>
        <button
          onClick={() => onPage(page - 1)}
          disabled={page <= 1}
          style={paginationBtn(page <= 1)}
        >
          ← Prev
        </button>
        <span style={{ padding: '6px 12px', fontWeight: 600, color: 'var(--dark)' }}>
          {page} / {pages}
        </span>
        <button
          onClick={() => onPage(page + 1)}
          disabled={page >= pages}
          style={paginationBtn(page >= pages)}
        >
          Next →
        </button>
      </div>
    </div>
  );
}

function paginationBtn(disabled: boolean): React.CSSProperties {
  return {
    padding: '6px 14px',
    borderRadius: 6,
    border: '1px solid var(--primary-10)',
    background: disabled ? '#f4f6f9' : '#fff',
    fontFamily: 'var(--fb)',
    fontSize: 12,
    color: disabled ? 'var(--mid)' : 'var(--primary)',
    cursor: disabled ? 'default' : 'pointer',
    opacity: disabled ? 0.5 : 1,
  };
}

// ─── Nav ─────────────────────────────────────────────────────────────────────

function Nav({
  page,
  setPage,
}: {
  page: Page;
  setPage: (p: Page) => void;
}) {
  const { user, logout } = useAuthStore();
  const navLink: React.CSSProperties = {
    background: 'none',
    border: 'none',
    color: 'rgba(255,255,255,0.45)',
    cursor: 'pointer',
    fontFamily: 'var(--fb)',
    fontSize: '9px',
    letterSpacing: '2px',
    textTransform: 'uppercase',
    padding: '5px 8px',
    borderRadius: '6px',
    transition: 'color 0.15s',
  };
  const navLinkActive: React.CSSProperties = {
    color: 'var(--gold-light)',
    backgroundColor: 'rgba(201,168,76,0.12)',
  };

  const pages: { id: Page; label: string }[] = [
    { id: 'dashboard', label: 'Risk Inventory' },
    { id: 'audit', label: 'Audit Log' },
    { id: 'data-sources', label: 'Data Sources' },
    { id: 'compliance', label: 'Compliance' },
    { id: 'info', label: 'Info' },
  ];

  return (
    <nav
      style={{
        backgroundColor: 'rgba(0,51,102,.97)',
        backdropFilter: 'blur(12px)',
        height: 52,
        position: 'sticky',
        top: 0,
        zIndex: 100,
        borderBottom: '1px solid rgba(255,255,255,.08)',
        padding: '0 40px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span>
          <span
            style={{
              fontFamily: "'Fraunces', Georgia, serif",
              fontSize: '20px',
              fontWeight: 300,
              color: '#ffffff',
            }}
          >
            O
          </span>
          <em
            style={{
              fontFamily: "'Fraunces', Georgia, serif",
              fontSize: '20px',
              fontWeight: 300,
              fontStyle: 'italic',
              color: 'var(--gold-light)',
            }}
          >
            PB
          </em>
        </span>
        <span
          style={{
            fontSize: 9,
            letterSpacing: '3px',
            textTransform: 'uppercase',
            color: 'rgba(255,255,255,.4)',
            fontFamily: 'var(--fb)',
          }}
        >
          PII Ghost-Hunter
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        {pages.map((p) => (
          <button
            key={p.id}
            style={page === p.id ? { ...navLink, ...navLinkActive } : navLink}
            onClick={() => setPage(p.id)}
          >
            {p.label}
          </button>
        ))}
        {user && (
          <span
            style={{
              fontFamily: 'var(--fb)',
              fontSize: 9,
              letterSpacing: '2px',
              color: 'rgba(255,255,255,0.3)',
              textTransform: 'uppercase',
              paddingLeft: 8,
              borderLeft: '1px solid rgba(255,255,255,0.1)',
            }}
          >
            {user.role}
          </span>
        )}
        <button
          onClick={logout}
          style={{
            background: 'none',
            border: '1px solid rgba(255,255,255,0.2)',
            borderRadius: '6px',
            color: 'rgba(255,255,255,0.5)',
            cursor: 'pointer',
            fontFamily: 'var(--fb)',
            fontSize: '9px',
            letterSpacing: '2px',
            textTransform: 'uppercase',
            padding: '5px 10px',
          }}
        >
          Logout
        </button>
      </div>
    </nav>
  );
}

// ─── Footer ───────────────────────────────────────────────────────────────────

function Footer() {
  const date = new Date()
    .toLocaleDateString('en-US', { year: 'numeric', month: 'long' })
    .toUpperCase();
  return (
    <footer
      style={{
        backgroundColor: 'var(--primary)',
        padding: '20px 48px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        fontFamily: 'var(--fb)',
        fontSize: '9px',
        letterSpacing: '3px',
        textTransform: 'uppercase',
        color: 'rgba(255,255,255,0.4)',
      }}
    >
      <span>OPB · Octavio Pérez Bravo · PII Ghost-Hunter</span>
      <span>{date}</span>
    </footer>
  );
}

// ─── Login page ───────────────────────────────────────────────────────────────

function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const login = useLogin();
  const demo = useDemoSeed();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    login.mutate({ email, password });
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        backgroundColor: '#003366',
        backgroundImage: `
          linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px)
        `,
        backgroundSize: '48px 48px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          background: '#fff',
          borderRadius: 16,
          width: 400,
          overflow: 'hidden',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
        }}
      >
        <div style={{ height: 3, background: 'var(--gold)' }} />
        <div style={{ padding: '40px 40px 36px' }}>
          <div style={{ textAlign: 'center', marginBottom: 32 }}>
            <span>
              <span
                style={{
                  fontFamily: "'Fraunces', Georgia, serif",
                  fontSize: 28,
                  fontWeight: 300,
                  color: '#003366',
                }}
              >
                O
              </span>
              <em
                style={{
                  fontFamily: "'Fraunces', Georgia, serif",
                  fontSize: 28,
                  fontWeight: 300,
                  fontStyle: 'italic',
                  color: 'var(--gold)',
                }}
              >
                PB
              </em>
            </span>
            <p
              style={{
                fontFamily: 'var(--fb)',
                fontSize: 9,
                letterSpacing: '3px',
                textTransform: 'uppercase',
                color: 'var(--mid)',
                marginTop: 8,
              }}
            >
              PII Ghost-Hunter
            </p>
          </div>

          <h2
            style={{
              fontFamily: "'Fraunces', Georgia, serif",
              fontSize: 22,
              fontWeight: 300,
              color: '#0a1628',
              marginBottom: 24,
              textAlign: 'center',
            }}
          >
            Sign <em style={{ fontStyle: 'italic', color: 'var(--gold)' }}>in</em>
          </h2>

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span
                style={{
                  fontFamily: 'var(--fb)',
                  fontSize: 9,
                  fontWeight: 600,
                  letterSpacing: '2px',
                  textTransform: 'uppercase',
                  color: 'var(--mid)',
                }}
              >
                Email
              </span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="dpo@company.com"
                style={{
                  padding: '10px 12px',
                  borderRadius: 8,
                  border: '1px solid var(--primary-10)',
                  fontSize: 14,
                  fontFamily: 'var(--fb)',
                  outline: 'none',
                }}
              />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span
                style={{
                  fontFamily: 'var(--fb)',
                  fontSize: 9,
                  fontWeight: 600,
                  letterSpacing: '2px',
                  textTransform: 'uppercase',
                  color: 'var(--mid)',
                }}
              >
                Password
              </span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                style={{
                  padding: '10px 12px',
                  borderRadius: 8,
                  border: '1px solid var(--primary-10)',
                  fontSize: 14,
                  fontFamily: 'var(--fb)',
                  outline: 'none',
                }}
              />
            </label>

            {login.isError && (
              <p
                style={{
                  fontFamily: 'var(--fb)',
                  fontSize: 13,
                  color: '#E03448',
                  textAlign: 'center',
                }}
              >
                Invalid credentials. Please try again.
              </p>
            )}

            <button
              type="submit"
              disabled={login.isPending || demo.isPending}
              style={{
                marginTop: 8,
                padding: '12px',
                borderRadius: 8,
                border: 'none',
                background: 'var(--primary)',
                color: '#fff',
                fontFamily: 'var(--fb)',
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: '1px',
                cursor: login.isPending || demo.isPending ? 'not-allowed' : 'pointer',
                opacity: login.isPending || demo.isPending ? 0.7 : 1,
              }}
            >
              {login.isPending ? 'Signing in…' : 'Sign in →'}
            </button>
          </form>

          {/* ── Divider ────────────────────────────────────────────────── */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              margin: '24px 0 0',
            }}
          >
            <div style={{ flex: 1, height: 1, background: 'var(--primary-10)' }} />
            <span
              style={{
                fontFamily: 'var(--fb)',
                fontSize: 10,
                letterSpacing: '2px',
                textTransform: 'uppercase',
                color: 'var(--mid)',
              }}
            >
              or
            </span>
            <div style={{ flex: 1, height: 1, background: 'var(--primary-10)' }} />
          </div>

          {/* ── Demo button ────────────────────────────────────────────── */}
          <div style={{ marginTop: 16 }}>
            {demo.isError && (
              <p
                style={{
                  fontFamily: 'var(--fb)',
                  fontSize: 12,
                  color: '#E03448',
                  textAlign: 'center',
                  marginBottom: 10,
                }}
              >
                {demo.error?.message ?? 'Could not load demo. Is DEMO_MODE=true?'}
              </p>
            )}
            <button
              type="button"
              onClick={() => demo.mutate()}
              disabled={demo.isPending || login.isPending}
              style={{
                width: '100%',
                padding: '12px',
                borderRadius: 8,
                border: '1.5px solid var(--gold)',
                background: demo.isPending ? 'rgba(200,152,42,0.08)' : 'transparent',
                color: 'var(--gold)',
                fontFamily: 'var(--fb)',
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: '1px',
                cursor: demo.isPending || login.isPending ? 'not-allowed' : 'pointer',
                opacity: demo.isPending || login.isPending ? 0.7 : 1,
                transition: 'background 0.15s',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 8,
              }}
            >
              {demo.isPending ? (
                <>
                  <span
                    style={{
                      width: 14,
                      height: 14,
                      border: '2px solid var(--gold)',
                      borderTopColor: 'transparent',
                      borderRadius: '50%',
                      display: 'inline-block',
                      animation: 'spin 0.7s linear infinite',
                    }}
                  />
                  Loading demo data…
                </>
              ) : (
                'Try Demo →'
              )}
            </button>
            <p
              style={{
                fontFamily: 'var(--fb)',
                fontSize: 11,
                color: 'var(--mid)',
                textAlign: 'center',
                marginTop: 8,
                lineHeight: 1.5,
              }}
            >
              Seeds 12 ghost tables across 5 data sources and signs in as DPO.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Risk Inventory (dashboard) ───────────────────────────────────────────────

const PII_CATEGORIES = [
  { value: '', label: 'All categories' },
  { value: 'EMAIL', label: 'Email' },
  { value: 'SSN', label: 'SSN' },
  { value: 'CREDIT_CARD', label: 'Credit Card' },
  { value: 'PHONE', label: 'Phone' },
  { value: 'FULL_NAME', label: 'Full Name' },
  { value: 'DATE_OF_BIRTH', label: 'Date of Birth' },
  { value: 'ADDRESS', label: 'Address' },
  { value: 'BANK_ACCOUNT', label: 'Bank Account' },
  { value: 'PASSPORT', label: 'Passport' },
];

const STATUSES = [
  { value: '', label: 'All statuses' },
  { value: 'flagged', label: 'Flagged' },
  { value: 'quarantined', label: 'Quarantined' },
  { value: 'remediated', label: 'Remediated' },
];

function DashboardPage({ onTableClick }: { onTableClick: (id: string) => void }) {
  const [filters, setFilters] = useState<RiskFilters>({ page: 1, size: 20 });
  const [source, setSource] = useState('');
  const [category, setCategory] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const { data: stats, isLoading: statsLoading } = useStatsSummary();
  const { data: risks, isLoading: risksLoading } = useRisks(filters);

  const applyFilters = () => {
    setFilters({
      page: 1,
      size: 20,
      source: source || undefined,
      pii_category: category || undefined,
      status: statusFilter || undefined,
    });
  };

  return (
    <>
      <Hero
        title="Privacy Risk"
        italic="Inventory"
        sub="Continuous detection and remediation of shadow PII across your data lake. Every flagged table, classified and queued for DPO review."
      />

      {/* KPI cards */}
      <div style={{ maxWidth: 1300, margin: '0 auto', padding: '40px 48px 0' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16 }}>
          <KpiCard
            value={statsLoading ? '—' : (stats?.total_flagged ?? 0)}
            label="Total Flagged"
            sub="Tables with PII detected"
          />
          <KpiCard
            value={statsLoading ? '—' : (stats?.remediated ?? 0)}
            label="Remediated"
            sub="Anonymized or quarantined"
          />
          <KpiCard
            value={statsLoading ? '—' : (stats?.pending_review ?? 0)}
            label="Pending Review"
            sub="Awaiting DPO action"
          />
          <KpiCard
            value={statsLoading ? '—' : `${stats?.compliance_score ?? 100}%`}
            label="Compliance Score"
            sub="GDPR / LGPD"
          />
        </div>
      </div>

      {/* Risk table */}
      <div style={{ maxWidth: 1300, margin: '0 auto', padding: '40px 48px 96px' }}>
        <div style={{ marginBottom: 20 }}>
          <Eyebrow>Risk Registry</Eyebrow>
          <h2
            style={{
              fontFamily: "'Fraunces', Georgia, serif",
              fontSize: 22,
              fontWeight: 300,
              color: '#0a1628',
              marginTop: 4,
            }}
          >
            Flagged <em style={{ fontStyle: 'italic' }}>tables</em>
          </h2>
          <p style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--mid)', marginTop: 4 }}>
            All tables where PII was detected with confidence ≥ 0.85
          </p>
        </div>

        {/* Filters */}
        <div
          style={{
            display: 'flex',
            gap: 16,
            flexWrap: 'wrap',
            alignItems: 'flex-end',
            marginBottom: 20,
            padding: '20px 24px',
            background: '#fff',
            borderRadius: 12,
            boxShadow: '0 1px 4px rgba(0,51,102,0.06)',
          }}
        >
          <FilterInput label="Data Source" value={source} onChange={setSource} placeholder="Search source…" />
          <FilterSelect label="PII Category" value={category} onChange={setCategory} options={PII_CATEGORIES} />
          <FilterSelect label="Status" value={statusFilter} onChange={setStatusFilter} options={STATUSES} />
          <button
            onClick={applyFilters}
            style={{
              padding: '8px 20px',
              borderRadius: 6,
              border: 'none',
              background: 'var(--primary)',
              color: '#fff',
              fontFamily: 'var(--fb)',
              fontSize: 12,
              fontWeight: 600,
              letterSpacing: '1px',
              cursor: 'pointer',
              alignSelf: 'flex-end',
              marginBottom: 1,
            }}
          >
            Apply →
          </button>
        </div>

        <div
          style={{
            background: '#fff',
            borderRadius: 12,
            boxShadow: '0 1px 4px rgba(0,51,102,0.08)',
            overflow: 'hidden',
          }}
        >
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <TableHead cols={['Table ID', 'Data Source', 'PII Categories', 'Max Confidence', 'Columns', 'Status', 'Last Scanned']} />
            <tbody>
              {risksLoading ? (
                Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} cols={7} />)
              ) : !risks?.items.length ? (
                <EmptyState message="No flagged tables yet. The patrol DAG will populate this inventory." />
              ) : (
                risks.items.map((r, i) => (
                  <tr
                    key={r.table_id}
                    style={{
                      background: i % 2 === 0 ? '#fff' : 'var(--primary-10)',
                      cursor: 'pointer',
                      transition: 'background 0.1s',
                    }}
                    onClick={() => onTableClick(r.table_id)}
                  >
                    <td style={tdStyle}>
                      <span
                        style={{
                          fontFamily: 'Courier New, monospace',
                          fontSize: 13,
                          color: 'var(--primary)',
                          fontWeight: 600,
                        }}
                      >
                        {r.table_id}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <span style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--dark)' }}>
                        {r.source_name}
                      </span>
                      <br />
                      <span style={{ fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--mid)' }}>
                        {r.data_source_type}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {r.pii_categories.slice(0, 3).map((c) => (
                          <span
                            key={c}
                            style={{
                              fontSize: 9,
                              fontFamily: 'var(--fb)',
                              fontWeight: 600,
                              letterSpacing: '1px',
                              textTransform: 'uppercase',
                              background: 'var(--primary-10)',
                              color: 'var(--primary)',
                              borderRadius: 4,
                              padding: '2px 6px',
                            }}
                          >
                            {c}
                          </span>
                        ))}
                        {r.pii_categories.length > 3 && (
                          <span style={{ fontSize: 11, color: 'var(--mid)' }}>
                            +{r.pii_categories.length - 3}
                          </span>
                        )}
                      </div>
                    </td>
                    <td style={tdStyle}>
                      <ConfidenceBar value={r.max_confidence} />
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'center' }}>
                      <span style={{ fontFamily: 'var(--fb)', fontSize: 13, fontWeight: 600, color: 'var(--dark)' }}>
                        {r.flagged_column_count}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <StatusBadge status={r.status} />
                    </td>
                    <td style={tdStyle}>
                      <span style={{ fontFamily: 'var(--fb)', fontSize: 12, color: 'var(--mid)' }}>
                        {new Date(r.last_scanned).toLocaleDateString()}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {risks && (
          <Pagination
            page={risks.page}
            pages={risks.pages}
            total={risks.total}
            onPage={(p) => setFilters((f) => ({ ...f, page: p }))}
          />
        )}
      </div>
    </>
  );
}

const tdStyle: React.CSSProperties = { padding: '12px 16px', borderBottom: '1px solid var(--primary-10)' };

// ─── PII Report page ──────────────────────────────────────────────────────────

function PIIReportPage({
  tableId,
  userRole,
  onBack,
}: {
  tableId: string;
  userRole: string;
  onBack: () => void;
}) {
  const { data: report, isLoading, error } = usePIIReport(tableId);
  const remediate = useRemediate(tableId);
  const token = useAuthStore((s) => s.token);
  const API = (import.meta as unknown as { env: Record<string,string> }).env?.VITE_API_URL ?? 'http://localhost:8000';

  const [modal, setModal] = useState<{ open: boolean; action: RemediateRequest['action'] | null }>({
    open: false,
    action: null,
  });
  const [lineage, setLineage] = useState<{ parents: LineageNode[]; children: LineageNode[] } | null>(null);
  const [lineageLoading, setLineageLoading] = useState(false);

  const canRemediate = userRole === 'dpo' || userRole === 'admin';

  const loadLineage = async () => {
    setLineageLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/tables/${tableId}/lineage`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.ok) setLineage(await r.json());
    } finally {
      setLineageLoading(false);
    }
  };

  const inferLineage = async () => {
    setLineageLoading(true);
    try {
      await fetch(`${API}/api/v1/tables/${tableId}/lineage/infer`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      await loadLineage();
    } finally {
      setLineageLoading(false);
    }
  };

  const handleConfirm = () => {
    if (!modal.action) return;
    remediate.mutate(
      { action: modal.action },
      { onSuccess: () => setModal({ open: false, action: null }) },
    );
  };

  return (
    <>
      <div
        style={{
          backgroundColor: '#003366',
          backgroundImage: `
            linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px)
          `,
          backgroundSize: '48px 48px',
        }}
      >
        <div style={{ maxWidth: 1300, margin: '0 auto', padding: '40px 48px 48px' }}>
          <button
            onClick={onBack}
            style={{
              background: 'none',
              border: '1px solid rgba(255,255,255,0.2)',
              borderRadius: 6,
              color: 'rgba(255,255,255,0.5)',
              cursor: 'pointer',
              fontFamily: 'var(--fb)',
              fontSize: 11,
              letterSpacing: '1px',
              padding: '5px 12px',
              marginBottom: 20,
            }}
          >
            ← Back to Inventory
          </button>
          <h1
            style={{
              fontFamily: "'Fraunces', Georgia, serif",
              fontSize: 40,
              fontWeight: 300,
              color: '#fff',
              lineHeight: 1.2,
              marginBottom: 8,
            }}
          >
            PII <em style={{ fontStyle: 'italic', color: 'var(--gold-light)' }}>Report</em>
          </h1>
          <p
            style={{
              fontFamily: 'Courier New, monospace',
              fontSize: 13,
              color: 'rgba(255,255,255,0.5)',
            }}
          >
            {tableId}
          </p>
          {report && (
            <p
              style={{
                fontFamily: 'var(--fb)',
                fontSize: 13,
                color: 'rgba(255,255,255,0.5)',
                marginTop: 6,
              }}
            >
              {report.source_name} · {report.data_source_type}
              {report.owner_email && ` · ${report.owner_email}`}
            </p>
          )}
        </div>
      </div>

      <div style={{ maxWidth: 1300, margin: '0 auto', padding: '40px 48px 96px' }}>
        {canRemediate && (
          <div
            style={{
              display: 'flex',
              gap: 12,
              marginBottom: 32,
              padding: '20px 24px',
              background: '#fff',
              borderRadius: 12,
              boxShadow: '0 1px 4px rgba(0,51,102,0.06)',
              borderLeft: '3px solid var(--gold)',
              alignItems: 'center',
              flexWrap: 'wrap',
            }}
          >
            <span
              style={{
                fontFamily: 'var(--fb)',
                fontSize: 12,
                fontWeight: 600,
                color: 'var(--mid)',
                letterSpacing: '1px',
                textTransform: 'uppercase',
                flex: 1,
              }}
            >
              Remediation actions
            </span>
            {(['anonymize', 'quarantine', 'false_positive'] as const).map((action) => (
              <button
                key={action}
                onClick={() => setModal({ open: true, action })}
                style={{
                  padding: '8px 18px',
                  borderRadius: 6,
                  border: '1px solid var(--primary-10)',
                  background: action === 'anonymize' ? 'var(--primary)' : '#fff',
                  color: action === 'anonymize' ? '#fff' : 'var(--primary)',
                  fontFamily: 'var(--fb)',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                  textTransform: 'capitalize',
                }}
              >
                {action === 'false_positive' ? 'Mark False Positive' : `${action.charAt(0).toUpperCase() + action.slice(1)} Now`}
              </button>
            ))}
          </div>
        )}

        <div style={{ marginBottom: 20 }}>
          <Eyebrow>Column Analysis</Eyebrow>
          <h2
            style={{
              fontFamily: "'Fraunces', Georgia, serif",
              fontSize: 22,
              fontWeight: 300,
              color: '#0a1628',
              marginTop: 4,
            }}
          >
            Flagged <em style={{ fontStyle: 'italic' }}>columns</em>
          </h2>
          <p style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--mid)', marginTop: 4 }}>
            Column-level PII classification results
          </p>
        </div>

        {error && (
          <div
            style={{
              padding: '20px 24px',
              background: '#FDEAEA',
              borderRadius: 12,
              color: '#7A1020',
              fontFamily: 'var(--fb)',
              fontSize: 13,
            }}
          >
            Failed to load report. The table may not exist.
          </div>
        )}

        <div
          style={{
            background: '#fff',
            borderRadius: 12,
            boxShadow: '0 1px 4px rgba(0,51,102,0.08)',
            overflow: 'hidden',
          }}
        >
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <TableHead cols={['Column Name', 'PII Category', 'Confidence', 'Samples', 'Status']} />
            <tbody>
              {isLoading ? (
                Array.from({ length: 4 }).map((_, i) => <SkeletonRow key={i} cols={5} />)
              ) : !report?.flagged_columns.length ? (
                <EmptyState message="No flagged columns found for this table." />
              ) : (
                report.flagged_columns.map((col, i) => (
                  <tr
                    key={col.column_name}
                    style={{ background: i % 2 === 0 ? '#fff' : 'var(--primary-10)' }}
                  >
                    <td style={tdStyle}>
                      <span
                        style={{
                          fontFamily: 'Courier New, monospace',
                          fontSize: 13,
                          color: 'var(--dark)',
                          fontWeight: 600,
                        }}
                      >
                        {col.column_name}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <span
                        style={{
                          fontSize: 9,
                          fontFamily: 'var(--fb)',
                          fontWeight: 700,
                          letterSpacing: '1.5px',
                          textTransform: 'uppercase',
                          background: '#FDEAEA',
                          color: '#7A1020',
                          borderRadius: 4,
                          padding: '3px 8px',
                        }}
                      >
                        {col.pii_category}
                      </span>
                    </td>
                    <td style={{ ...tdStyle, minWidth: 180 }}>
                      <ConfidenceBar value={col.confidence} />
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'center' }}>
                      <span style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--mid)' }}>
                        {col.sample_count ?? '—'}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <StatusBadge status={col.status} />
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Lineage section ─────────────────────────────────────────────── */}
      <div style={{ maxWidth: 1300, margin: '0 auto', padding: '0 48px 48px' }}>
        <div
          style={{
            background: '#fff',
            borderRadius: 12,
            boxShadow: '0 1px 4px rgba(0,51,102,0.08)',
            padding: '28px 32px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
            <div>
              <Eyebrow>F-06 · Data Lineage</Eyebrow>
              <div style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 20, fontWeight: 300, color: 'var(--dark)' }}>
                Table Lineage Map
              </div>
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button
                onClick={lineage ? loadLineage : inferLineage}
                disabled={lineageLoading}
                style={{
                  padding: '8px 18px', borderRadius: 8, border: '1.5px solid var(--primary)',
                  background: 'transparent', color: 'var(--primary)', cursor: 'pointer',
                  fontFamily: 'var(--fb)', fontSize: 12, fontWeight: 600, letterSpacing: '1px',
                  opacity: lineageLoading ? 0.6 : 1,
                }}
              >
                {lineageLoading ? 'Loading…' : lineage ? 'Refresh' : 'Infer Lineage'}
              </button>
            </div>
          </div>

          {!lineage && !lineageLoading && (
            <p style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--mid)', textAlign: 'center', padding: '24px 0' }}>
              Click "Infer Lineage" to run the path-heuristic analysis and detect upstream sources and downstream copies.
            </p>
          )}

          {lineage && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
              {(['parents', 'children'] as const).map((side) => (
                <div key={side}>
                  <div style={{ fontFamily: 'var(--fb)', fontSize: 10, fontWeight: 600, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--mid)', marginBottom: 12 }}>
                    {side === 'parents' ? '↑ Upstream sources' : '↓ Downstream copies'}
                  </div>
                  {lineage[side].length === 0 ? (
                    <div style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--mid)', fontStyle: 'italic' }}>None detected</div>
                  ) : (
                    lineage[side].map((node) => (
                      <div key={node.table_id} style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        padding: '10px 14px', borderRadius: 8, background: 'var(--light)',
                        marginBottom: 8, gap: 12,
                      }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--dark)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {node.source_name || node.table_id}
                          </div>
                          <div style={{ fontFamily: 'var(--fb)', fontSize: 10, color: 'var(--mid)', marginTop: 2 }}>
                            {node.inference_method.replace(/_/g, ' ')} · {Math.round(node.confidence * 100)}% confidence
                          </div>
                        </div>
                        <StatusBadge status={node.status} />
                      </div>
                    ))
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <ConfirmModal
        open={modal.open}
        title={`Confirm: ${modal.action}`}
        message={`This will queue a ${modal.action} action for table ${tableId}. The remediation DAG will process it in the next run. This action is logged in the audit trail.`}
        onConfirm={handleConfirm}
        onCancel={() => setModal({ open: false, action: null })}
        loading={remediate.isPending}
      />
    </>
  );
}

// ─── Audit Log page ───────────────────────────────────────────────────────────

const EVENT_TYPES = [
  { value: '', label: 'All event types' },
  { value: 'anonymization_completed', label: 'Anonymization completed' },
  { value: 'quarantine_completed', label: 'Quarantine completed' },
  { value: 'remediation_completed', label: 'Remediation completed' },
  { value: 'manual_anonymize_requested', label: 'Manual anonymize' },
  { value: 'manual_quarantine_requested', label: 'Manual quarantine' },
  { value: 'manual_false_positive_requested', label: 'False positive' },
  { value: 'manual_review_requested', label: 'Manual review' },
];

function AuditPage() {
  const token = useAuthStore((s) => s.token);
  const [filters, setFilters] = useState<AuditFilters>({ page: 1, size: 50 });
  const [actorInput, setActorInput] = useState('');
  const [eventTypeInput, setEventTypeInput] = useState('');
  const { data, isLoading } = useAuditLog(filters);

  const applyFilters = () => {
    setFilters({
      page: 1,
      size: 50,
      actor: actorInput || undefined,
      event_type: eventTypeInput || undefined,
    });
  };

  return (
    <>
      <Hero
        title="Audit"
        italic="Log"
        sub="Immutable record of every detection, classification, and remediation action. Append-only — no edits or deletions permitted."
      />

      <div style={{ maxWidth: 1300, margin: '0 auto', padding: '40px 48px 96px' }}>
        <div
          style={{
            display: 'flex',
            gap: 16,
            flexWrap: 'wrap',
            alignItems: 'flex-end',
            marginBottom: 20,
            padding: '20px 24px',
            background: '#fff',
            borderRadius: 12,
            boxShadow: '0 1px 4px rgba(0,51,102,0.06)',
          }}
        >
          <FilterInput label="Actor" value={actorInput} onChange={setActorInput} placeholder="system, dpo@…" />
          <FilterSelect
            label="Event Type"
            value={eventTypeInput}
            onChange={setEventTypeInput}
            options={EVENT_TYPES}
          />
          <button
            onClick={applyFilters}
            style={{
              padding: '8px 20px',
              borderRadius: 6,
              border: 'none',
              background: 'var(--primary)',
              color: '#fff',
              fontFamily: 'var(--fb)',
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
              alignSelf: 'flex-end',
              marginBottom: 1,
            }}
          >
            Apply →
          </button>
          <button
            onClick={() => exportAuditLog(token, { actor: actorInput || undefined, event_type: eventTypeInput || undefined })}
            style={{
              padding: '8px 20px',
              borderRadius: 6,
              border: '1px solid var(--primary-10)',
              background: '#fff',
              color: 'var(--primary)',
              fontFamily: 'var(--fb)',
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
              alignSelf: 'flex-end',
              marginBottom: 1,
            }}
          >
            Export CSV ↓
          </button>
        </div>

        <div style={{ marginBottom: 20 }}>
          <Eyebrow>Compliance Trail</Eyebrow>
          <h2
            style={{
              fontFamily: "'Fraunces', Georgia, serif",
              fontSize: 22,
              fontWeight: 300,
              color: '#0a1628',
              marginTop: 4,
            }}
          >
            All system <em style={{ fontStyle: 'italic' }}>actions</em>
          </h2>
        </div>

        <div
          style={{
            background: '#fff',
            borderRadius: 12,
            boxShadow: '0 1px 4px rgba(0,51,102,0.08)',
            overflow: 'hidden',
          }}
        >
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <TableHead cols={['Timestamp', 'Event Type', 'Table ID', 'Actor']} />
            <tbody>
              {isLoading ? (
                Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={4} />)
              ) : !data?.items.length ? (
                <EmptyState message="No audit events recorded yet." />
              ) : (
                data.items.map((entry, i) => (
                  <tr
                    key={entry.id}
                    style={{ background: i % 2 === 0 ? '#fff' : 'var(--primary-10)' }}
                  >
                    <td style={tdStyle}>
                      <span style={{ fontFamily: 'Courier New, monospace', fontSize: 12, color: 'var(--mid)' }}>
                        {new Date(entry.timestamp).toISOString().replace('T', ' ').slice(0, 19)}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <span
                        style={{
                          fontFamily: 'var(--fb)',
                          fontSize: 12,
                          fontWeight: 600,
                          color: 'var(--dark)',
                          letterSpacing: '0.5px',
                        }}
                      >
                        {entry.event_type}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      {entry.table_id ? (
                        <span
                          style={{
                            fontFamily: 'Courier New, monospace',
                            fontSize: 12,
                            color: 'var(--primary)',
                          }}
                        >
                          {entry.table_id}
                        </span>
                      ) : (
                        <span style={{ color: 'var(--mid)', fontSize: 12 }}>—</span>
                      )}
                    </td>
                    <td style={tdStyle}>
                      <span style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--mid)' }}>
                        {entry.actor}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {data && (
          <Pagination
            page={data.page}
            pages={Math.ceil(data.total / data.size)}
            total={data.total}
            onPage={(p) => setFilters((f) => ({ ...f, page: p }))}
          />
        )}
      </div>
    </>
  );
}

// ─── Data Sources page ────────────────────────────────────────────────────────

function DataSourcesPage({ onSourceClick }: { onSourceClick: (source: string) => void }) {
  const { data, isLoading } = useDataSources();

  const maxFlagged = Math.max(...(data?.items.map((d) => d.flagged_count) ?? [1]), 1);

  return (
    <>
      <Hero
        title="Data Source"
        italic="Map"
        sub="PII risk footprint across your cloud infrastructure. Click any source to view its flagged tables in the Risk Inventory."
      />

      <div style={{ maxWidth: 1300, margin: '0 auto', padding: '40px 48px 96px' }}>
        <div style={{ marginBottom: 24 }}>
          <Eyebrow>Infrastructure View</Eyebrow>
          <h2
            style={{
              fontFamily: "'Fraunces', Georgia, serif",
              fontSize: 22,
              fontWeight: 300,
              color: '#0a1628',
              marginTop: 4,
            }}
          >
            Shadow data <em style={{ fontStyle: 'italic' }}>footprint</em>
          </h2>
          <p style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--mid)', marginTop: 4 }}>
            Grouped by data source — heat intensity represents PII risk concentration
          </p>
        </div>

        {isLoading ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                style={{
                  background: '#fff',
                  borderRadius: 12,
                  padding: 24,
                  height: 140,
                  boxShadow: '0 1px 4px rgba(0,51,102,0.06)',
                }}
              />
            ))}
          </div>
        ) : !data?.items.length ? (
          <div
            style={{
              padding: '56px',
              textAlign: 'center',
              background: '#fff',
              borderRadius: 12,
              fontFamily: 'var(--fb)',
              fontSize: 13,
              color: 'var(--mid)',
            }}
          >
            No data sources found. The scanner DAG will populate this once tables are detected.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
            {data.items.map((ds) => {
              const intensity = ds.flagged_count / maxFlagged;
              const bg =
                intensity > 0.7
                  ? '#FDEAEA'
                  : intensity > 0.4
                    ? '#FEF0E6'
                    : intensity > 0.1
                      ? '#E0EAF4'
                      : '#F4F6F9';
              const borderColor =
                intensity > 0.7
                  ? '#E03448'
                  : intensity > 0.4
                    ? '#F07020'
                    : intensity > 0.1
                      ? '#003366'
                      : '#99BBDD';

              return (
                <div
                  key={ds.source_name}
                  onClick={() => onSourceClick(ds.source_name)}
                  style={{
                    background: bg,
                    borderRadius: 12,
                    padding: '20px 24px',
                    cursor: 'pointer',
                    borderLeft: `3px solid ${borderColor}`,
                    boxShadow: '0 1px 4px rgba(0,51,102,0.06)',
                    transition: 'transform 0.1s, box-shadow 0.1s',
                  }}
                >
                  <div
                    style={{
                      fontFamily: 'var(--fb)',
                      fontSize: 9,
                      fontWeight: 700,
                      letterSpacing: '2px',
                      textTransform: 'uppercase',
                      color: borderColor,
                      marginBottom: 8,
                    }}
                  >
                    {ds.data_source_type}
                  </div>
                  <div
                    style={{
                      fontFamily: "'Fraunces', Georgia, serif",
                      fontSize: 16,
                      fontWeight: 400,
                      color: '#0a1628',
                      marginBottom: 4,
                      wordBreak: 'break-all',
                    }}
                  >
                    {ds.source_name}
                  </div>
                  {ds.bucket && (
                    <div
                      style={{
                        fontFamily: 'Courier New, monospace',
                        fontSize: 11,
                        color: 'var(--mid)',
                        marginBottom: 12,
                      }}
                    >
                      {ds.bucket}
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 16, marginTop: 12 }}>
                    <div>
                      <div
                        style={{
                          fontFamily: "'Fraunces', Georgia, serif",
                          fontSize: 22,
                          fontWeight: 300,
                          color: borderColor,
                          lineHeight: 1,
                        }}
                      >
                        {ds.flagged_count}
                      </div>
                      <div
                        style={{
                          fontFamily: 'var(--fb)',
                          fontSize: 9,
                          letterSpacing: '1.5px',
                          textTransform: 'uppercase',
                          color: 'var(--mid)',
                        }}
                      >
                        Flagged
                      </div>
                    </div>
                    <div>
                      <div
                        style={{
                          fontFamily: "'Fraunces', Georgia, serif",
                          fontSize: 22,
                          fontWeight: 300,
                          color: '#0a1628',
                          lineHeight: 1,
                        }}
                      >
                        {ds.table_count}
                      </div>
                      <div
                        style={{
                          fontFamily: 'var(--fb)',
                          fontSize: 9,
                          letterSpacing: '1.5px',
                          textTransform: 'uppercase',
                          color: 'var(--mid)',
                        }}
                      >
                        Total
                      </div>
                    </div>
                  </div>
                  {ds.pii_categories.length > 0 && (
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 12 }}>
                      {ds.pii_categories.slice(0, 4).map((c) => (
                        <span
                          key={c}
                          style={{
                            fontSize: 8,
                            fontFamily: 'var(--fb)',
                            fontWeight: 700,
                            letterSpacing: '1px',
                            textTransform: 'uppercase',
                            background: 'rgba(0,51,102,0.08)',
                            color: 'var(--primary)',
                            borderRadius: 3,
                            padding: '2px 5px',
                          }}
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

// ─── Info Page ────────────────────────────────────────────────────────────────

function InfoPage() {
  const [view, setView] = useState<'business' | 'engineering'>('business');

  const tabBar: React.CSSProperties = {
    display: 'flex',
    gap: 4,
    padding: '0 48px',
    borderBottom: '1px solid rgba(0,51,102,0.1)',
    backgroundColor: '#fff',
  };
  const tab = (active: boolean): React.CSSProperties => ({
    background: 'none',
    border: 'none',
    borderBottom: active ? '2px solid var(--gold)' : '2px solid transparent',
    color: active ? 'var(--dark)' : 'var(--mid)',
    cursor: 'pointer',
    fontFamily: 'var(--fb)',
    fontSize: 11,
    fontWeight: active ? 600 : 400,
    letterSpacing: '2px',
    textTransform: 'uppercase',
    padding: '14px 16px',
    marginBottom: -1,
    transition: 'color 0.15s',
  });

  return (
    <>
      <Hero
        title="Platform"
        italic="Overview"
        sub="Architectural context and business rationale for the PII Ghost-Hunter platform — two lenses, same system."
      />
      <div style={tabBar}>
        <button style={tab(view === 'business')} onClick={() => setView('business')}>
          Business View
        </button>
        <button style={tab(view === 'engineering')} onClick={() => setView('engineering')}>
          Engineering View
        </button>
      </div>

      <div style={{ maxWidth: 1300, margin: '0 auto', padding: '48px 48px 80px' }}>
        {view === 'business' ? <BusinessView /> : <EngineeringView />}
      </div>
    </>
  );
}

// ── Business View ──────────────────────────────────────────────────────────────

function BusinessView() {
  const card: React.CSSProperties = {
    background: '#fff',
    borderRadius: 12,
    boxShadow: '0 1px 4px rgba(0,51,102,0.08)',
    padding: '32px 36px',
    marginBottom: 32,
  };
  const h2: React.CSSProperties = {
    fontFamily: "'Fraunces', Georgia, serif",
    fontSize: 26,
    fontWeight: 300,
    color: 'var(--dark)',
    marginBottom: 16,
  };
  const h3: React.CSSProperties = {
    fontFamily: 'var(--fb)',
    fontSize: 12,
    fontWeight: 600,
    letterSpacing: '2px',
    textTransform: 'uppercase',
    color: 'var(--primary)',
    marginBottom: 10,
    marginTop: 24,
  };
  const p: React.CSSProperties = {
    fontFamily: 'var(--fb)',
    fontSize: 14,
    color: '#374151',
    lineHeight: 1.8,
    marginBottom: 12,
  };
  const li: React.CSSProperties = {
    fontFamily: 'var(--fb)',
    fontSize: 14,
    color: '#374151',
    lineHeight: 1.75,
    paddingLeft: 8,
    marginBottom: 6,
  };

  const painPoints = [
    {
      icon: '⚠',
      title: 'Uncontrolled data copies',
      body:
        'Development pipelines, analytics workloads, and backup processes routinely create copies of production tables. These copies accumulate across S3 buckets, Athena catalogs, and data lake partitions without any ownership or lifecycle policy attached to them.',
    },
    {
      icon: '🔍',
      title: 'No visibility into PII exposure',
      body:
        'Data governance teams have no systematic way to know which tables contain personal data. Manual audits are too slow, require specialist effort, and go stale within days as new datasets arrive.',
    },
    {
      icon: '⚖',
      title: 'Regulatory pressure without tooling',
      body:
        'GDPR and LGPD impose deletion, minimization, and audit obligations. Regulators expect organizations to demonstrate control over where personal data lives. Without automated scanning, responding to a Subject Access Request or a regulatory inquiry requires weeks of manual effort.',
    },
    {
      icon: '🔔',
      title: 'Late detection, reactive response',
      body:
        'When a data breach or a compliance audit surfaces an overlooked dataset, the remediation cycle is costly — legal review, breach notifications, and operational disruption are all avoidable if the data was never retained unmanaged in the first place.',
    },
  ];

  const advantages = [
    {
      label: 'Continuous coverage',
      detail:
        'Every new table or file that enters the data lake is evaluated. The scanner listens to Kafka events in real-time so nothing is added without being assessed.',
    },
    {
      label: 'High-confidence classification',
      detail:
        'A fine-tuned multilingual DistilBERT model classifies 10 PII categories with a minimum confidence threshold of 0.85 before any automated action is taken, keeping false-positive rates low.',
    },
    {
      label: 'Non-invasive by design',
      detail:
        'Sampling jobs use read-only IAM policies and never touch production traffic. The platform observes data without modifying source systems.',
    },
    {
      label: 'Immutable audit trail',
      detail:
        'Every detection, quarantine action, and remediation step is written to an append-only log at the database level. The log cannot be altered by any application role, satisfying Article 30 documentation requirements.',
    },
    {
      label: 'DPO-centric workflow',
      detail:
        'When high-confidence PII is detected, the Data Protection Officer receives a structured notification containing the table name, PII categories, confidence scores, data owner, and a direct link to the dashboard action panel — no digging through logs required.',
    },
    {
      label: 'Graduated remediation',
      detail:
        'The platform offers three resolution paths: automatic anonymization via PySpark, quarantine to a restricted S3 bucket pending DPO review, or marking a detection as a false positive to feed back into model improvement.',
    },
  ];

  return (
    <>
      {/* Value proposition */}
      <div style={card}>
        <Eyebrow>Core Value Proposition</Eyebrow>
        <h2 style={h2}>What the platform does</h2>
        <p style={p}>
          PII Ghost-Hunter is a continuous data governance platform that automatically discovers, classifies, and remediates unmanaged copies of sensitive data across cloud data infrastructure. It connects to the event stream of a data lake, identifies every new dataset that arrives, samples its contents against a trained PII classifier, and initiates a remediation workflow when personal data is found — without requiring any manual triage.
        </p>
        <p style={p}>
          The intended audience is mid-to-large organizations operating cloud data platforms under GDPR or LGPD obligations, where the volume of data creation outpaces manual governance capacity. The platform is not a one-time audit tool; it runs continuously and generates a living compliance record as data moves through the organization.
        </p>

        {/* Flow diagram */}
        <div style={{ marginTop: 28 }}>
          <div
            style={{
              fontSize: 10,
              fontFamily: 'var(--fb)',
              letterSpacing: '2px',
              textTransform: 'uppercase',
              color: 'var(--mid)',
              marginBottom: 16,
            }}
          >
            End-to-end flow
          </div>
          <BusinessFlowDiagram />
        </div>
      </div>

      {/* Pain points */}
      <div style={card}>
        <Eyebrow>Pain Points Addressed</Eyebrow>
        <h2 style={h2}>What breaks without this</h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: 20,
            marginTop: 8,
          }}
        >
          {painPoints.map((pt) => (
            <div
              key={pt.title}
              style={{
                border: '1px solid rgba(0,51,102,0.1)',
                borderRadius: 10,
                padding: '20px 24px',
                borderLeft: '3px solid var(--gold)',
              }}
            >
              <div
                style={{
                  fontSize: 22,
                  marginBottom: 10,
                }}
              >
                {pt.icon}
              </div>
              <div
                style={{
                  fontFamily: 'var(--fb)',
                  fontSize: 13,
                  fontWeight: 600,
                  color: 'var(--dark)',
                  marginBottom: 8,
                }}
              >
                {pt.title}
              </div>
              <div style={{ ...p, marginBottom: 0, fontSize: 13 }}>{pt.body}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Advantages */}
      <div style={card}>
        <Eyebrow>Platform Advantages</Eyebrow>
        <h2 style={h2}>How it addresses those problems</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {advantages.map((a, i) => (
            <div
              key={a.label}
              style={{
                display: 'grid',
                gridTemplateColumns: '220px 1fr',
                gap: 24,
                padding: '18px 0',
                borderBottom:
                  i < advantages.length - 1 ? '1px solid rgba(0,51,102,0.07)' : 'none',
              }}
            >
              <div
                style={{
                  fontFamily: 'var(--fb)',
                  fontSize: 12,
                  fontWeight: 600,
                  color: 'var(--primary)',
                  paddingTop: 2,
                }}
              >
                {a.label}
              </div>
              <div style={{ ...p, marginBottom: 0 }}>{a.detail}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Business outcomes */}
      <div style={card}>
        <Eyebrow>Business Outcomes</Eyebrow>
        <h2 style={h2}>Measurable results</h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: 16,
            marginTop: 8,
          }}
        >
          {[
            { metric: 'Time to detection', before: 'Weeks (manual audit)', after: 'Hours (automated scan)' },
            { metric: 'DPO triage effort', before: 'Full dataset review', after: 'Structured notification + one-click action' },
            { metric: 'Audit trail completeness', before: 'Inconsistent, manual', after: 'Append-only, query-ready' },
            { metric: 'Regulatory response time', before: 'Days–weeks', after: 'Immediate export from audit log' },
          ].map((row) => (
            <div
              key={row.metric}
              style={{
                background: 'var(--light)',
                borderRadius: 10,
                padding: '20px 20px',
              }}
            >
              <div
                style={{
                  fontFamily: 'var(--fb)',
                  fontSize: 10,
                  letterSpacing: '2px',
                  textTransform: 'uppercase',
                  color: 'var(--mid)',
                  marginBottom: 12,
                }}
              >
                {row.metric}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 8,
                    fontSize: 12,
                    fontFamily: 'var(--fb)',
                    color: '#7A1020',
                  }}
                >
                  <span style={{ marginTop: 1, flexShrink: 0 }}>✕</span>
                  <span>{row.before}</span>
                </div>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 8,
                    fontSize: 12,
                    fontFamily: 'var(--fb)',
                    color: '#0D5C3A',
                  }}
                >
                  <span style={{ marginTop: 1, flexShrink: 0 }}>✓</span>
                  <span>{row.after}</span>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div style={{ ...h3, marginTop: 32 }}>Who uses it</div>
        <ul style={{ listStyle: 'none', paddingLeft: 0 }}>
          {[
            ['Data Protection Officer (DPO)', 'Receives prioritized notifications, takes remediation actions, exports audit evidence for regulators.'],
            ['Data Platform / Engineering team', 'Monitors scanner health, manages model versions, reviews false positives to improve classifier accuracy.'],
            ['Security & Compliance Auditor', 'Queries the immutable audit log for evidence of control effectiveness, exports CSV for regulatory submissions.'],
            ['Engineering Leadership', 'Tracks overall compliance posture via KPI dashboard — percentage remediated, open findings, trend over time.'],
          ].map(([role, desc]) => (
            <li key={role} style={{ ...li, display: 'grid', gridTemplateColumns: '220px 1fr', gap: 24, paddingLeft: 0, marginBottom: 10 }}>
              <span style={{ fontWeight: 600, color: 'var(--primary)', fontSize: 13 }}>{role}</span>
              <span>{desc}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

function BusinessFlowDiagram() {
  const box = (label: string, sub: string, accent: string): React.ReactNode => (
    <div
      style={{
        background: '#fff',
        border: `1.5px solid ${accent}`,
        borderRadius: 10,
        padding: '14px 18px',
        minWidth: 140,
        textAlign: 'center',
        flex: '1 1 0',
      }}
    >
      <div
        style={{
          fontFamily: 'var(--fb)',
          fontSize: 12,
          fontWeight: 600,
          color: 'var(--dark)',
          marginBottom: 4,
        }}
      >
        {label}
      </div>
      <div style={{ fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--mid)' }}>{sub}</div>
    </div>
  );

  const arrow = (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        color: 'var(--mid)',
        fontSize: 18,
        padding: '0 4px',
        flexShrink: 0,
      }}
    >
      →
    </div>
  );

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'stretch',
        gap: 0,
        flexWrap: 'wrap',
        rowGap: 12,
      }}
    >
      {box('New Dataset Arrives', 'Table created or file moved in data lake', '#99BBDD')}
      {arrow}
      {box('Scanner Event', 'Kafka event captured, persisted to queue', '#99BBDD')}
      {arrow}
      {box('Column Sampling', 'Stratified sample (max 1,000 rows/column) written to S3', '#C8982A')}
      {arrow}
      {box('PII Classification', 'DistilBERT model scores 10 PII categories per column', '#C8982A')}
      {arrow}
      {box('Confidence Gate', 'Score ≥ 0.85 triggers automated action', '#E03448')}
      {arrow}
      {box('Remediation', 'Anonymize, quarantine, or mark false positive — DPO notified', '#27B97C')}
    </div>
  );
}

// ── Engineering View ───────────────────────────────────────────────────────────

function EngineeringView() {
  const card: React.CSSProperties = {
    background: '#fff',
    borderRadius: 12,
    boxShadow: '0 1px 4px rgba(0,51,102,0.08)',
    padding: '32px 36px',
    marginBottom: 32,
  };
  const h2: React.CSSProperties = {
    fontFamily: "'Fraunces', Georgia, serif",
    fontSize: 26,
    fontWeight: 300,
    color: 'var(--dark)',
    marginBottom: 16,
  };
  const p: React.CSSProperties = {
    fontFamily: 'var(--fb)',
    fontSize: 14,
    color: '#374151',
    lineHeight: 1.8,
    marginBottom: 12,
  };

  const stackRows: { layer: string; components: string[] }[] = [
    { layer: 'Presentation', components: ['React 18 + TypeScript', 'Vite', 'TanStack Query', 'CSS-in-JS (inline styles)'] },
    { layer: 'API Gateway', components: ['FastAPI (Python 3.11)', 'SQLAlchemy 2.0 async', 'Pydantic v2', 'JWT / OAuth2 RBAC'] },
    { layer: 'Event Streaming', components: ['Apache Kafka 7.5', 'Confluent Schema Registry', 'Avro schemas', 'Dead-letter topic'] },
    { layer: 'Orchestration', components: ['Apache Airflow 2.8.1', '4 DAGs (patrol, sampling, remediation, expiry)', 'Custom PIIClassifierOperator'] },
    { layer: 'ML / Inference', components: ['DistilBERT-base-multilingual-cased', 'HuggingFace Transformers', 'PyTorch', 'MLflow (registry + tracking)'] },
    { layer: 'Data Processing', components: ['PySpark 3.5 (anonymization)', 'AWS Athena / Glue (sampling)', 'boto3 (S3 ops)'] },
    { layer: 'Storage', components: ['PostgreSQL 15 (primary state)', 'Redis 7 (caching)', 'S3 (staging, quarantine, model artifacts)'] },
    { layer: 'Observability', components: ['Prometheus (metrics)', 'Grafana (dashboards)', 'Structlog (JSON logs — no PII values)'] },
    { layer: 'Infrastructure', components: ['Docker Compose (local dev)', 'Terraform (AWS provisioning)', 'Helm + Kubernetes (production)', 'GitHub Actions (CI/CD)'] },
  ];

  const layerColors: Record<string, string> = {
    Presentation: '#E0EAF4',
    'API Gateway': '#D6E8D6',
    'Event Streaming': '#FEF0E6',
    Orchestration: '#EDE8F7',
    'ML / Inference': '#FFF3CD',
    'Data Processing': '#E0F7EF',
    Storage: '#F4F6F9',
    Observability: '#FDE8EC',
    Infrastructure: '#E8EAF0',
  };

  const stateTransitions = [
    { from: 'pending', to: 'queued', trigger: 'Patrol DAG picks up event' },
    { from: 'queued', to: 'classified', trigger: 'Sampling + inference complete' },
    { from: 'classified', to: 'flagged', trigger: 'Confidence ≥ 0.85, PII found' },
    { from: 'classified', to: 'clean', trigger: 'No PII detected' },
    { from: 'flagged', to: 'quarantined', trigger: 'DPO quarantine action or auto-remediation' },
    { from: 'flagged', to: 'remediated', trigger: 'DPO anonymize action' },
    { from: 'quarantined', to: 'remediated', trigger: 'DPO approves deletion/anonymization after review' },
  ];

  return (
    <>
      {/* Architecture diagram */}
      <div style={card}>
        <Eyebrow>System Architecture</Eyebrow>
        <h2 style={h2}>How the system is structured</h2>
        <p style={p}>
          The platform is composed of five loosely coupled subsystems connected through a Kafka event bus and a shared PostgreSQL state store. Each subsystem can be deployed, scaled, and restarted independently.
        </p>
        <ArchitectureDiagram />
      </div>

      {/* Tech stack */}
      <div style={card}>
        <Eyebrow>Tech Stack</Eyebrow>
        <h2 style={h2}>Component inventory by layer</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 8 }}>
          {stackRows.map((row) => (
            <div
              key={row.layer}
              style={{
                display: 'grid',
                gridTemplateColumns: '160px 1fr',
                gap: 16,
                alignItems: 'start',
                padding: '14px 16px',
                borderRadius: 8,
                background: layerColors[row.layer] ?? '#F4F6F9',
              }}
            >
              <div
                style={{
                  fontFamily: 'var(--fb)',
                  fontSize: 11,
                  fontWeight: 600,
                  color: 'var(--primary)',
                  letterSpacing: '1px',
                  textTransform: 'uppercase',
                  paddingTop: 2,
                }}
              >
                {row.layer}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {row.components.map((c) => (
                  <span
                    key={c}
                    style={{
                      background: 'rgba(255,255,255,0.75)',
                      border: '1px solid rgba(0,51,102,0.12)',
                      borderRadius: 6,
                      padding: '3px 10px',
                      fontFamily: 'var(--fb)',
                      fontSize: 12,
                      color: 'var(--dark)',
                    }}
                  >
                    {c}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* End-to-end workflow */}
      <div style={card}>
        <Eyebrow>End-to-End Workflow</Eyebrow>
        <h2 style={h2}>Execution path from event to remediation</h2>

        {[
          {
            step: '01',
            title: 'Event ingestion',
            detail:
              'A Kafka consumer listens to two topics: table.created (emitted by catalog systems when a new table appears in Athena or Glue) and file.moved (emitted by AWS EventBridge when objects land in designated S3 prefixes). Each event is validated against an Avro schema, persisted to the scanner_events table with status=pending, and re-published to the pii.candidates topic for downstream consumption. The consumer is idempotent — duplicate events on the same event_id are silently upserted.',
          },
          {
            step: '02',
            title: 'Patrol DAG',
            detail:
              'An Airflow DAG runs on a daily schedule and queries scanner_events for records created in the last 24 hours with status=pending. It issues a compare-and-swap UPDATE (pending → queued) to claim each record, avoiding double-processing across DAG retries. The DAG then triggers the sampling pipeline for each claimed event.',
          },
          {
            step: '03',
            title: 'Column sampling',
            detail:
              'The sampling pipeline reads the target table via AWS Athena or directly from S3 Parquet using Glue metadata. It performs stratified random sampling up to 1,000 rows per column, writes the sample as Parquet to the staging S3 bucket, and persists a column_samples record pointing to the S3 path. IAM policies on the sampling role are read-only; no production data is moved or modified.',
          },
          {
            step: '04',
            title: 'PII inference',
            detail:
              'The custom PIIClassifierOperator calls the inference microservice (FastAPI, port 8001) with batches of up to 50 columns. The service loads a fine-tuned DistilBERT-base-multilingual-cased model from S3 (lazy-loaded and cached in memory). For each column it returns a predicted PII category and a confidence score. Results are written to pii_findings. Prometheus counters track classification latency and throughput per request.',
          },
          {
            step: '05',
            title: 'Confidence gate',
            detail:
              'If any column scores ≥ 0.85 confidence, the remediation DAG is triggered automatically. Findings below the threshold are stored as classified without action, awaiting potential manual review. The 0.85 threshold was chosen empirically during evaluation against a labeled test set containing known PII fixtures (VISA PANs, Brazilian CPF numbers, US SSNs) to minimize false positives while maintaining recall above 0.90.',
          },
          {
            step: '06',
            title: 'Remediation',
            detail:
              'The remediation DAG runs two branches in parallel: (a) a PySpark job reads the flagged columns, applies per-category anonymization strategies (SHA-256 hash for email, last-4 masking for credit cards, full redaction for SSN/CPF, format-preserving pseudonymization for names), and writes the anonymized dataset back to S3; (b) a quarantine job moves the raw flagged data to the pii-quarantine bucket under a write-only policy, creating a quarantine_manifest record. Both branches write append-only entries to audit_log on completion.',
          },
          {
            step: '07',
            title: 'DPO notification',
            detail:
              'After the remediation branches complete, the DPO notifier sends a Jinja-templated email and a Slack webhook containing the table name, PII categories detected, confidence scores, data owner contact, recommended action, and a direct link to the dashboard PII Report panel. The notifier retries up to three times with exponential backoff on delivery failure.',
          },
          {
            step: '08',
            title: 'Quarantine expiry',
            detail:
              'A separate daily DAG checks quarantine_manifest records. Seven days before the 30-day retention window expires, it sends a reminder to the DPO. On expiry, records without an approved remediation are auto-deleted from the quarantine bucket and the manifest is updated. This implements the GDPR storage limitation principle without requiring ongoing manual review.',
          },
        ].map((s, i, arr) => (
          <div
            key={s.step}
            style={{
              display: 'grid',
              gridTemplateColumns: '48px 1fr',
              gap: 20,
              paddingBottom: i < arr.length - 1 ? 24 : 0,
              marginBottom: i < arr.length - 1 ? 24 : 0,
              borderBottom: i < arr.length - 1 ? '1px solid rgba(0,51,102,0.07)' : 'none',
            }}
          >
            <div
              style={{
                fontFamily: "'Fraunces', Georgia, serif",
                fontSize: 28,
                fontWeight: 300,
                color: 'var(--primary-30)',
                lineHeight: 1,
                paddingTop: 4,
              }}
            >
              {s.step}
            </div>
            <div>
              <div
                style={{
                  fontFamily: 'var(--fb)',
                  fontSize: 14,
                  fontWeight: 600,
                  color: 'var(--dark)',
                  marginBottom: 8,
                }}
              >
                {s.title}
              </div>
              <div style={{ fontFamily: 'var(--fb)', fontSize: 13, color: '#374151', lineHeight: 1.8 }}>
                {s.detail}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* State machine */}
      <div style={card}>
        <Eyebrow>Data Lifecycle</Eyebrow>
        <h2 style={h2}>Scanner event state machine</h2>
        <p style={{ ...p, marginBottom: 24 }}>
          Every scanner event moves through a defined set of states. State transitions are recorded in audit_log. The scanner_events table uses a compare-and-swap pattern on status updates to prevent race conditions between concurrent DAG runs.
        </p>
        <StateMachineDiagram states={stateTransitions} />
      </div>

      {/* Data model */}
      <div style={card}>
        <Eyebrow>Data Model</Eyebrow>
        <h2 style={h2}>Core PostgreSQL tables</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
          {[
            {
              table: 'scanner_events',
              purpose: 'One record per detected dataset. Tracks lifecycle from ingestion to remediation.',
              keyFields: ['event_id (PK)', 'source_name', 'data_source_type', 'status', 'owner_email', 'raw_event JSONB'],
            },
            {
              table: 'column_samples',
              purpose: 'Metadata for each sampled column — points to S3 Parquet for the sample data.',
              keyFields: ['table_id (FK)', 'column_name', 'sample_s3_path', 'status', 'sample_count'],
            },
            {
              table: 'pii_findings',
              purpose: 'One record per column per table. Stores the ML classification result.',
              keyFields: ['table_id (FK)', 'column_name', 'pii_category', 'confidence', 'flagged', 'status'],
            },
            {
              table: 'audit_log',
              purpose: 'Append-only compliance trail. DB trigger prevents UPDATE and DELETE.',
              keyFields: ['event_type', 'table_id', 'actor', 'timestamp', 'details_json JSONB'],
            },
            {
              table: 'quarantine_manifest',
              purpose: 'Tracks every quarantine action — source path, destination, review status.',
              keyFields: ['table_id (FK)', 'source_s3_path', 'quarantine_s3_path', 'flagged_categories', 'reviewed_by'],
            },
            {
              table: 'model_registry',
              purpose: 'Versioned ML model catalog. Production inference uses the single approved model.',
              keyFields: ['version', 's3_uri', 'macro_f1', 'weighted_f1', 'accuracy', 'status'],
            },
          ].map((t) => (
            <div
              key={t.table}
              style={{
                border: '1px solid rgba(0,51,102,0.12)',
                borderRadius: 10,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  background: 'var(--primary)',
                  padding: '10px 16px',
                  fontFamily: 'monospace',
                  fontSize: 12,
                  color: 'var(--gold-light)',
                  letterSpacing: '0.5px',
                }}
              >
                {t.table}
              </div>
              <div style={{ padding: '14px 16px' }}>
                <div
                  style={{
                    fontFamily: 'var(--fb)',
                    fontSize: 12,
                    color: '#374151',
                    lineHeight: 1.6,
                    marginBottom: 12,
                  }}
                >
                  {t.purpose}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {t.keyFields.map((f) => (
                    <div
                      key={f}
                      style={{
                        fontFamily: 'monospace',
                        fontSize: 11,
                        color: 'var(--primary-60)',
                        background: 'var(--light)',
                        borderRadius: 4,
                        padding: '3px 8px',
                        display: 'inline-block',
                        width: 'fit-content',
                      }}
                    >
                      {f}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Key design decisions */}
      <div style={card}>
        <Eyebrow>Design Decisions</Eyebrow>
        <h2 style={h2}>Non-obvious architectural choices</h2>
        {[
          {
            decision: 'Append-only audit log enforced at the database level',
            rationale:
              'A DB trigger that blocks UPDATE and DELETE on audit_log is more trustworthy than application-level enforcement. Even a compromised application role cannot alter the audit trail. This directly satisfies the immutability requirement expected by regulators reviewing GDPR Article 30 records.',
          },
          {
            decision: 'Confidence threshold of 0.85 for automated action',
            rationale:
              'Evaluated on a labeled synthetic dataset covering VISA PANs, Brazilian CPF numbers, US SSNs, and email addresses. Below 0.85, the false-positive rate increased enough to create DPO alert fatigue. Above 0.90, recall dropped for ambiguous column names like "user_ref" containing real CPF values. 0.85 was the best balance found during evaluation.',
          },
          {
            decision: 'Read-only IAM policy for sampling jobs',
            rationale:
              'Sampling jobs only need to read production tables to extract a statistical sample. Granting write access would expose production data to accidental modification. By using separate read-only credentials at the IAM level, even a bug in the sampling code cannot modify source data.',
          },
          {
            decision: 'Kafka as the integration bus rather than polling',
            rationale:
              'New tables arrive asynchronously and at unpredictable rates. Polling the Glue catalog or S3 on a fixed schedule would introduce latency proportional to the poll interval and generate unnecessary API calls. Kafka event subscriptions give sub-second detection with no polling overhead.',
          },
          {
            decision: 'DistilBERT instead of rule-based detection',
            rationale:
              'Rule-based regex classifiers fail on obfuscated column names (e.g., "field_a"), truncated values, and multilingual content. DistilBERT classifies based on the statistical distribution of values in the column sample, not column names, and handles Portuguese and English PII natively via the multilingual checkpoint.',
          },
          {
            decision: 'Idempotent DAGs with compare-and-swap status updates',
            rationale:
              'Airflow DAGs can be re-triggered manually or automatically on failure. Without idempotency, a retry would re-sample and re-classify already-processed tables, creating duplicate findings and audit entries. The compare-and-swap UPDATE (status=pending → queued only if current value is pending) guarantees exactly-once processing across retries.',
          },
        ].map((d, i, arr) => (
          <div
            key={d.decision}
            style={{
              paddingBottom: i < arr.length - 1 ? 24 : 0,
              marginBottom: i < arr.length - 1 ? 24 : 0,
              borderBottom: i < arr.length - 1 ? '1px solid rgba(0,51,102,0.07)' : 'none',
            }}
          >
            <div
              style={{
                fontFamily: 'var(--fb)',
                fontSize: 13,
                fontWeight: 600,
                color: 'var(--dark)',
                marginBottom: 8,
                display: 'flex',
                alignItems: 'flex-start',
                gap: 10,
              }}
            >
              <span
                style={{
                  background: 'var(--primary)',
                  color: 'var(--gold-light)',
                  borderRadius: 4,
                  padding: '1px 7px',
                  fontSize: 10,
                  fontWeight: 600,
                  letterSpacing: '1px',
                  flexShrink: 0,
                  marginTop: 2,
                }}
              >
                ADR
              </span>
              {d.decision}
            </div>
            <div style={{ fontFamily: 'var(--fb)', fontSize: 13, color: '#374151', lineHeight: 1.8 }}>
              {d.rationale}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function ArchitectureDiagram() {
  const node = (
    label: string,
    sub: string,
    bg: string,
    border: string,
    textColor = 'var(--dark)'
  ): React.ReactNode => (
    <div
      style={{
        background: bg,
        border: `1.5px solid ${border}`,
        borderRadius: 10,
        padding: '12px 16px',
        textAlign: 'center',
        minWidth: 130,
        flex: '1 1 0',
      }}
    >
      <div
        style={{ fontFamily: 'var(--fb)', fontSize: 12, fontWeight: 600, color: textColor, marginBottom: 4 }}
      >
        {label}
      </div>
      <div style={{ fontFamily: 'var(--fb)', fontSize: 10, color: 'var(--mid)' }}>{sub}</div>
    </div>
  );

  const arrow = (label = '') => (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '0 4px',
        flexShrink: 0,
        gap: 2,
      }}
    >
      <div style={{ fontFamily: 'var(--fb)', fontSize: 18, color: 'var(--mid)', lineHeight: 1 }}>→</div>
      {label && (
        <div
          style={{
            fontFamily: 'var(--fb)',
            fontSize: 9,
            color: 'var(--mid)',
            letterSpacing: '1px',
            textTransform: 'uppercase',
            textAlign: 'center',
            maxWidth: 60,
          }}
        >
          {label}
        </div>
      )}
    </div>
  );

  const sectionLabel = (text: string) => (
    <div
      style={{
        fontFamily: 'var(--fb)',
        fontSize: 9,
        letterSpacing: '2px',
        textTransform: 'uppercase',
        color: 'var(--mid)',
        marginBottom: 8,
        marginTop: 20,
      }}
    >
      {text}
    </div>
  );

  return (
    <div>
      {sectionLabel('Ingestion layer')}
      <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap', rowGap: 8 }}>
        {node('Data Lake', 'S3 / Athena / Glue', '#E0EAF4', '#99BBDD')}
        {arrow('events')}
        {node('Kafka', 'table.created file.moved', '#FEF0E6', '#F07020')}
        {arrow('consume')}
        {node('Scanner Consumer', 'Python Kafka consumer', '#E0EAF4', '#336699')}
        {arrow('persist')}
        {node('PostgreSQL', 'scanner_events table', '#E0F7EF', '#27B97C')}
      </div>

      {sectionLabel('Processing layer')}
      <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap', rowGap: 8 }}>
        {node('Airflow', 'Patrol + Sampling DAGs', '#EDE8F7', '#7C5CBF')}
        {arrow('sample')}
        {node('S3 Staging', 'Parquet column samples', '#FFF3CD', '#C8982A')}
        {arrow('classify')}
        {node('Inference Service', 'FastAPI + DistilBERT', '#FEF0E6', '#E03448')}
        {arrow('results')}
        {node('pii_findings', 'PostgreSQL table', '#E0F7EF', '#27B97C')}
      </div>

      {sectionLabel('Remediation layer')}
      <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap', rowGap: 8 }}>
        {node('Remediation DAG', 'Airflow, confidence ≥ 0.85', '#EDE8F7', '#7C5CBF')}
        {arrow('anonymize')}
        {node('PySpark Job', 'Anonymization engine', '#FEF0E6', '#F07020')}
        {arrow()}
        {node('S3 Quarantine', 'Restricted bucket', '#FFF3CD', '#C8982A')}
        {arrow('notify')}
        {node('DPO / Slack / Email', 'Notification + audit log', '#E0EAF4', '#336699')}
      </div>

      {sectionLabel('API & presentation layer')}
      <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap', rowGap: 8 }}>
        {node('FastAPI', 'REST API, JWT RBAC', '#E0EAF4', '#336699')}
        {arrow('serve')}
        {node('React Dashboard', 'Risk Inventory, PII Report, Audit, Sources', '#E0EAF4', '#003366')}
      </div>
    </div>
  );
}

function StateMachineDiagram({
  states,
}: {
  states: { from: string; to: string; trigger: string }[];
}) {
  const statusColors: Record<string, { bg: string; border: string; text: string }> = {
    pending:      { bg: '#E0EAF4', border: '#336699', text: '#003366' },
    queued:       { bg: '#EDE8F7', border: '#7C5CBF', text: '#4A2F8A' },
    classified:   { bg: '#FFF3CD', border: '#C8982A', text: '#7A5800' },
    flagged:      { bg: '#FDEAEA', border: '#E03448', text: '#7A1020' },
    clean:        { bg: '#E0F7EF', border: '#27B97C', text: '#0D5C3A' },
    quarantined:  { bg: '#FEF0E6', border: '#F07020', text: '#7A3800' },
    remediated:   { bg: '#E0F7EF', border: '#27B97C', text: '#0D5C3A' },
  };

  const stateBox = (status: string) => {
    const s = statusColors[status] ?? statusColors['pending'];
    return (
      <span
        style={{
          display: 'inline-block',
          background: s.bg,
          border: `1.5px solid ${s.border}`,
          borderRadius: 6,
          padding: '2px 10px',
          fontFamily: 'monospace',
          fontSize: 11,
          color: s.text,
          fontWeight: 600,
          whiteSpace: 'nowrap',
        }}
      >
        {status}
      </span>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {states.map((t) => (
        <div
          key={`${t.from}-${t.to}`}
          style={{
            display: 'grid',
            gridTemplateColumns: '120px 24px 120px 1fr',
            alignItems: 'center',
            gap: 12,
            padding: '10px 16px',
            background: 'var(--light)',
            borderRadius: 8,
          }}
        >
          {stateBox(t.from)}
          <span style={{ color: 'var(--mid)', fontSize: 16, textAlign: 'center' }}>→</span>
          {stateBox(t.to)}
          <span style={{ fontFamily: 'var(--fb)', fontSize: 12, color: '#374151' }}>{t.trigger}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Shared types for new features ───────────────────────────────────────────

interface LineageNode {
  table_id: string;
  source_name: string;
  confidence: number;
  inference_method: string;
  status: string;
}

interface RopaEntry {
  source_name: string;
  data_source_type: string;
  owner_email: string;
  pii_categories: string[];
  first_detected_at: string | null;
  last_scanned_at: string | null;
  current_status: string;
  purpose: string | null;
  legal_basis: string | null;
  cross_border_transfer: boolean;
}

interface TrendPoint {
  week_start: string;
  compliance_score: number;
  new_flagged: number;
  new_remediated: number;
}

// ─── Compliance Page ──────────────────────────────────────────────────────────

function CompliancePage() {
  const [tab, setTab] = useState<'ropa' | 'dsar' | 'intelligence'>('ropa');
  const token = useAuthStore((s) => s.token);
  const API = (import.meta as unknown as { env: Record<string,string> }).env?.VITE_API_URL ?? 'http://localhost:8000';

  const tabBar: React.CSSProperties = {
    display: 'flex',
    gap: 4,
    padding: '0 48px',
    borderBottom: '1px solid rgba(0,51,102,0.1)',
    backgroundColor: '#fff',
  };
  const tabStyle = (active: boolean): React.CSSProperties => ({
    background: 'none',
    border: 'none',
    borderBottom: active ? '2px solid var(--gold)' : '2px solid transparent',
    color: active ? 'var(--dark)' : 'var(--mid)',
    cursor: 'pointer',
    fontFamily: 'var(--fb)',
    fontSize: 11,
    fontWeight: active ? 600 : 400,
    letterSpacing: '2px',
    textTransform: 'uppercase',
    padding: '14px 16px',
    marginBottom: -1,
    transition: 'color 0.15s',
  });

  return (
    <>
      <Hero
        title="Compliance"
        italic="Hub"
        sub="ROPA register, data subject search, and live compliance intelligence — all derived from detected PII findings."
      />
      <div style={tabBar}>
        <button style={tabStyle(tab === 'ropa')} onClick={() => setTab('ropa')}>
          ROPA Register
        </button>
        <button style={tabStyle(tab === 'dsar')} onClick={() => setTab('dsar')}>
          Data Subject Search
        </button>
        <button style={tabStyle(tab === 'intelligence')} onClick={() => setTab('intelligence')}>
          Compliance Intelligence
        </button>
      </div>
      <div style={{ maxWidth: 1300, margin: '0 auto', padding: '48px 48px 80px' }}>
        {tab === 'ropa' && <RopaTab token={token} API={API} />}
        {tab === 'dsar' && <DsarTab token={token} API={API} />}
        {tab === 'intelligence' && <IntelligenceTab token={token} API={API} />}
      </div>
    </>
  );
}

// ── ROPA Tab ──────────────────────────────────────────────────────────────────

function RopaTab({ token, API }: { token: string | null; API: string }) {
  const [entries, setEntries] = useState<RopaEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [legalBases, setLegalBases] = useState<string[]>([]);
  const [annotating, setAnnotating] = useState<string | null>(null);
  const [form, setForm] = useState({ purpose: '', legal_basis: '', cross_border_transfer: false });

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/compliance/ropa`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.ok) {
        const d = await r.json();
        setEntries(d.entries ?? []);
        setLegalBases(d.legal_bases ?? []);
      }
    } finally {
      setLoading(false);
    }
  };

  const saveAnnotation = async (sourceName: string) => {
    await fetch(`${API}/api/v1/compliance/ropa/${encodeURIComponent(sourceName)}`, {
      method: 'PATCH',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    });
    setAnnotating(null);
    await load();
  };

  useState(() => { load(); });

  const card: React.CSSProperties = {
    background: '#fff',
    borderRadius: 12,
    boxShadow: '0 1px 4px rgba(0,51,102,0.08)',
    overflow: 'hidden',
    marginBottom: 24,
  };
  const incomplete = entries.filter((e) => !e.purpose).length;

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <Eyebrow>GDPR Art. 30</Eyebrow>
          <h2 style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 26, fontWeight: 300, color: 'var(--dark)' }}>
            Records of Processing Activities
          </h2>
          {incomplete > 0 && (
            <p style={{ fontFamily: 'var(--fb)', fontSize: 12, color: '#E03448', marginTop: 6 }}>
              {incomplete} {incomplete === 1 ? 'entry requires' : 'entries require'} purpose annotation
            </p>
          )}
        </div>
        <a
          href={`${API}/api/v1/compliance/ropa/export.csv`}
          style={{
            padding: '10px 20px', borderRadius: 8, background: 'var(--primary)',
            color: '#fff', fontFamily: 'var(--fb)', fontSize: 12, fontWeight: 600,
            letterSpacing: '1px', textDecoration: 'none',
          }}
        >
          Export CSV
        </a>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 40, fontFamily: 'var(--fb)', color: 'var(--mid)' }}>Loading ROPA…</div>
      ) : (
        <div style={card}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--light)' }}>
                {['Data Source', 'Type', 'PII Categories', 'Status', 'Purpose', 'Legal Basis', ''].map((h) => (
                  <th key={h} style={{
                    fontFamily: 'var(--fb)', fontSize: 9, fontWeight: 600, letterSpacing: '2px',
                    textTransform: 'uppercase', color: 'var(--mid)', padding: '12px 16px', textAlign: 'left',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <>
                  <tr key={e.source_name} style={{ borderTop: '1px solid rgba(0,51,102,0.07)' }}>
                    <td style={{ padding: '14px 16px', fontFamily: 'monospace', fontSize: 12, color: 'var(--dark)' }}>
                      {e.source_name}
                    </td>
                    <td style={{ padding: '14px 16px' }}>
                      <span style={{ fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--mid)', textTransform: 'uppercase', letterSpacing: '1px' }}>
                        {e.data_source_type}
                      </span>
                    </td>
                    <td style={{ padding: '14px 16px' }}>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {e.pii_categories.map((c) => (
                          <span key={c} style={{ background: 'var(--primary-10)', color: 'var(--primary)', borderRadius: 4, padding: '2px 7px', fontSize: 10, fontFamily: 'var(--fb)', fontWeight: 600 }}>
                            {c}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td style={{ padding: '14px 16px' }}><StatusBadge status={e.current_status} /></td>
                    <td style={{ padding: '14px 16px', fontFamily: 'var(--fb)', fontSize: 12, color: e.purpose ? 'var(--dark)' : '#E03448' }}>
                      {e.purpose ?? '⚠ Not documented'}
                    </td>
                    <td style={{ padding: '14px 16px', fontFamily: 'var(--fb)', fontSize: 12, color: e.legal_basis ? 'var(--dark)' : 'var(--mid)' }}>
                      {e.legal_basis ?? '—'}
                    </td>
                    <td style={{ padding: '14px 16px' }}>
                      <button
                        onClick={() => {
                          setAnnotating(annotating === e.source_name ? null : e.source_name);
                          setForm({ purpose: e.purpose ?? '', legal_basis: e.legal_basis ?? '', cross_border_transfer: e.cross_border_transfer });
                        }}
                        style={{ background: 'none', border: '1px solid var(--primary-30)', borderRadius: 6, padding: '4px 12px', fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--primary)', cursor: 'pointer' }}
                      >
                        {annotating === e.source_name ? 'Cancel' : 'Annotate'}
                      </button>
                    </td>
                  </tr>
                  {annotating === e.source_name && (
                    <tr key={`${e.source_name}-form`}>
                      <td colSpan={7} style={{ background: 'var(--primary-10)', padding: '20px 24px' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto auto', gap: 12, alignItems: 'end' }}>
                          <div>
                            <div style={{ fontFamily: 'var(--fb)', fontSize: 9, fontWeight: 600, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--mid)', marginBottom: 6 }}>Processing Purpose</div>
                            <input
                              value={form.purpose}
                              onChange={(e) => setForm((f) => ({ ...f, purpose: e.target.value }))}
                              placeholder="e.g. Customer analytics, payroll processing"
                              style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--primary-30)', fontFamily: 'var(--fb)', fontSize: 13 }}
                            />
                          </div>
                          <div>
                            <div style={{ fontFamily: 'var(--fb)', fontSize: 9, fontWeight: 600, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--mid)', marginBottom: 6 }}>Legal Basis</div>
                            <select
                              value={form.legal_basis}
                              onChange={(ev) => setForm((f) => ({ ...f, legal_basis: ev.target.value }))}
                              style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--primary-30)', fontFamily: 'var(--fb)', fontSize: 13 }}
                            >
                              <option value="">— Select —</option>
                              {legalBases.map((lb) => <option key={lb} value={lb}>{lb}</option>)}
                            </select>
                          </div>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--fb)', fontSize: 12, color: 'var(--dark)', paddingBottom: 8, cursor: 'pointer' }}>
                            <input
                              type="checkbox"
                              checked={form.cross_border_transfer}
                              onChange={(ev) => setForm((f) => ({ ...f, cross_border_transfer: ev.target.checked }))}
                            />
                            Cross-border transfer
                          </label>
                          <button
                            onClick={() => saveAnnotation(e.source_name)}
                            style={{ padding: '9px 20px', borderRadius: 8, background: 'var(--primary)', color: '#fff', border: 'none', fontFamily: 'var(--fb)', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}
                          >
                            Save
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// ── DSAR Tab ──────────────────────────────────────────────────────────────────

function DsarTab({ token, API }: { token: string | null; API: string }) {
  const [identifierType, setIdentifierType] = useState('email');
  const [identifierValue, setIdentifierValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ search_id: string; tables_matched: number; pii_category_searched: string; matches: { source_name: string; table_id: string; data_source_type: string; estimated_row_count: number; status: string; confidence: number }[] } | null>(null);
  const [history, setHistory] = useState<{ search_id: string; initiated_by: string; identifier_type: string; tables_matched_count: number; created_at: string }[]>([]);

  const loadHistory = async () => {
    const r = await fetch(`${API}/api/v1/dsar/searches`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (r.ok) {
      const d = await r.json();
      setHistory(d.searches ?? []);
    }
  };

  useState(() => { loadHistory(); });

  const runSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!identifierValue.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const r = await fetch(`${API}/api/v1/dsar/search`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier_type: identifierType, identifier_value: identifierValue }),
      });
      if (r.ok) {
        setResult(await r.json());
        await loadHistory();
      }
    } finally {
      setLoading(false);
      setIdentifierValue('');
    }
  };

  return (
    <>
      <div style={{ marginBottom: 32 }}>
        <Eyebrow>GDPR Art. 15</Eyebrow>
        <h2 style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 26, fontWeight: 300, color: 'var(--dark)', marginBottom: 8 }}>
          Data Subject Search
        </h2>
        <p style={{ fontFamily: 'var(--fb)', fontSize: 13, color: 'var(--mid)', lineHeight: 1.7, maxWidth: 600 }}>
          Identify all detected data assets containing records for a specific individual. The search term is never stored — only a SHA-256 hash is logged in the audit trail.
        </p>
      </div>

      <div style={{ background: '#fff', borderRadius: 12, boxShadow: '0 1px 4px rgba(0,51,102,0.08)', padding: '28px 32px', marginBottom: 32 }}>
        <form onSubmit={runSearch} style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontFamily: 'var(--fb)', fontSize: 9, fontWeight: 600, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--mid)', marginBottom: 6 }}>Identifier Type</div>
            <select
              value={identifierType}
              onChange={(e) => setIdentifierType(e.target.value)}
              style={{ padding: '10px 14px', borderRadius: 8, border: '1px solid var(--primary-10)', fontFamily: 'var(--fb)', fontSize: 13, minWidth: 160 }}
            >
              <option value="email">Email Address</option>
              <option value="national_id">National ID / SSN / CPF</option>
              <option value="phone">Phone Number</option>
            </select>
          </div>
          <div style={{ flex: 1, minWidth: 260 }}>
            <div style={{ fontFamily: 'var(--fb)', fontSize: 9, fontWeight: 600, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--mid)', marginBottom: 6 }}>Identifier Value</div>
            <input
              type={identifierType === 'email' ? 'email' : 'text'}
              value={identifierValue}
              onChange={(e) => setIdentifierValue(e.target.value)}
              placeholder={identifierType === 'email' ? 'data.subject@example.com' : identifierType === 'phone' ? '+55 11 99999-0000' : '123.456.789-09'}
              required
              style={{ width: '100%', padding: '10px 14px', borderRadius: 8, border: '1px solid var(--primary-10)', fontFamily: 'var(--fb)', fontSize: 13 }}
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            style={{ padding: '10px 28px', borderRadius: 8, background: 'var(--primary)', color: '#fff', border: 'none', fontFamily: 'var(--fb)', fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer', opacity: loading ? 0.7 : 1 }}
          >
            {loading ? 'Searching…' : 'Search Data Lake →'}
          </button>
        </form>
      </div>

      {result && (
        <div style={{ background: '#fff', borderRadius: 12, boxShadow: '0 1px 4px rgba(0,51,102,0.08)', padding: '28px 32px', marginBottom: 32 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
            <div style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 32, fontWeight: 300, color: result.tables_matched > 0 ? '#E03448' : '#27B97C' }}>
              {result.tables_matched}
            </div>
            <div>
              <div style={{ fontFamily: 'var(--fb)', fontSize: 13, fontWeight: 600, color: 'var(--dark)' }}>
                {result.tables_matched > 0 ? 'Tables contain this PII category' : 'No tables found for this PII category'}
              </div>
              <div style={{ fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--mid)' }}>
                Searched for: {result.pii_category_searched} · Search ID: {result.search_id.slice(0, 8)}…
              </div>
            </div>
          </div>
          {result.matches.length > 0 && (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--light)' }}>
                  {['Data Source', 'Type', 'Est. Rows', 'Confidence', 'Status'].map((h) => (
                    <th key={h} style={{ fontFamily: 'var(--fb)', fontSize: 9, fontWeight: 600, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--mid)', padding: '10px 14px', textAlign: 'left' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.matches.map((m) => (
                  <tr key={m.table_id} style={{ borderTop: '1px solid rgba(0,51,102,0.07)' }}>
                    <td style={{ padding: '12px 14px', fontFamily: 'monospace', fontSize: 12 }}>{m.source_name}</td>
                    <td style={{ padding: '12px 14px', fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--mid)', textTransform: 'uppercase', letterSpacing: '1px' }}>{m.data_source_type}</td>
                    <td style={{ padding: '12px 14px', fontFamily: 'var(--fb)', fontSize: 12 }}>{m.estimated_row_count?.toLocaleString() ?? '—'}</td>
                    <td style={{ padding: '12px 14px' }}><ConfidenceBar value={m.confidence} /></td>
                    <td style={{ padding: '12px 14px' }}><StatusBadge status={m.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p style={{ fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--mid)', marginTop: 16, lineHeight: 1.6 }}>
            These tables contain the detected PII category. Row-level presence of the specific identifier requires manual investigation. This search is logged in the audit trail.
          </p>
        </div>
      )}

      {history.length > 0 && (
        <div style={{ background: '#fff', borderRadius: 12, boxShadow: '0 1px 4px rgba(0,51,102,0.08)', padding: '28px 32px' }}>
          <Eyebrow>Search History</Eyebrow>
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 12 }}>
            <thead>
              <tr style={{ background: 'var(--light)' }}>
                {['Time', 'Type', 'By', 'Tables Matched'].map((h) => (
                  <th key={h} style={{ fontFamily: 'var(--fb)', fontSize: 9, fontWeight: 600, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--mid)', padding: '10px 14px', textAlign: 'left' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.slice(0, 10).map((s) => (
                <tr key={s.search_id} style={{ borderTop: '1px solid rgba(0,51,102,0.07)' }}>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--fb)', fontSize: 12, color: 'var(--mid)' }}>
                    {new Date(s.created_at).toLocaleString()}
                  </td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--fb)', fontSize: 12, textTransform: 'capitalize' }}>{s.identifier_type.replace(/_/g, ' ')}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--fb)', fontSize: 12, color: 'var(--mid)' }}>{s.initiated_by}</td>
                  <td style={{ padding: '10px 14px', fontFamily: "'Fraunces', Georgia, serif", fontSize: 20, fontWeight: 300, color: s.tables_matched_count > 0 ? '#E03448' : 'var(--mid)' }}>
                    {s.tables_matched_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// ── Compliance Intelligence Tab ───────────────────────────────────────────────

function SparkLine({ scores }: { scores: number[] }) {
  if (scores.length < 2) return null;
  const w = 320;
  const h = 64;
  const min = Math.min(...scores, 0);
  const max = Math.max(...scores, 100);
  const range = max - min || 1;
  const xs = scores.map((_, i) => (i / (scores.length - 1)) * w);
  const ys = scores.map((v) => h - ((v - min) / range) * (h - 8) - 4);
  const points = xs.map((x, i) => `${x},${ys[i]}`).join(' ');
  const last = scores[scores.length - 1];
  const first = scores[0];
  const trend = last - first;
  const color = trend >= 0 ? '#27B97C' : '#E03448';
  return (
    <div>
      <svg width={w} height={h} style={{ overflow: 'visible', display: 'block' }}>
        <polyline points={points} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={xs[xs.length - 1]} cy={ys[ys.length - 1]} r={4} fill={color} />
      </svg>
      <div style={{ fontFamily: 'var(--fb)', fontSize: 10, color, marginTop: 4 }}>
        {trend >= 0 ? '▲' : '▼'} {Math.abs(Math.round(trend))}pp over 12 weeks
      </div>
    </div>
  );
}

function IntelligenceTab({ token, API }: { token: string | null; API: string }) {
  const [trends, setTrends] = useState<{ trend: TrendPoint[]; avg_ttr_days: number | null } | null>(null);
  const [forecast, setForecast] = useState<{ current_score: number; projected_score_30d: number; remediations_per_week: number; total_pending: number; days_to_full_compliance: number | null } | null>(null);
  const [exposure, setExposure] = useState<{ total_exposed_records: number; estimated_fine_low_eur: number; estimated_fine_high_eur: number; methodology: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useState(() => {
    const h = { Authorization: `Bearer ${token}` };
    Promise.all([
      fetch(`${API}/api/v1/compliance/trends`, { headers: h }).then((r) => r.ok ? r.json() : null),
      fetch(`${API}/api/v1/compliance/forecast`, { headers: h }).then((r) => r.ok ? r.json() : null),
      fetch(`${API}/api/v1/compliance/risk-exposure`, { headers: h }).then((r) => r.ok ? r.json() : null),
    ]).then(([t, f, e]) => {
      setTrends(t);
      setForecast(f);
      setExposure(e);
      setLoading(false);
    });
  });

  const card: React.CSSProperties = {
    background: '#fff', borderRadius: 12,
    boxShadow: '0 1px 4px rgba(0,51,102,0.08)',
    padding: '28px 32px',
  };

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 60, fontFamily: 'var(--fb)', color: 'var(--mid)' }}>Loading compliance intelligence…</div>;
  }

  const scores = trends?.trend.map((t) => t.compliance_score) ?? [];
  const currentScore = forecast?.current_score ?? 0;
  const scoreColor = currentScore >= 90 ? '#27B97C' : currentScore >= 70 ? '#F07020' : '#E03448';

  return (
    <>
      <Eyebrow>Live Analytics</Eyebrow>
      <h2 style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 26, fontWeight: 300, color: 'var(--dark)', marginBottom: 32 }}>
        Compliance Intelligence
      </h2>

      {/* KPI row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 32 }}>
        {[
          { label: 'Compliance Score', value: `${currentScore}%`, color: scoreColor, sub: 'current' },
          { label: 'Projected (30d)', value: `${forecast?.projected_score_30d ?? '—'}%`, color: 'var(--primary)', sub: `at ${forecast?.remediations_per_week ?? 0} remediations/week` },
          { label: 'Open Findings', value: forecast?.total_pending ?? '—', color: '#E03448', sub: 'pending remediation' },
          { label: 'Avg. Time to Remediation', value: trends?.avg_ttr_days != null ? `${trends.avg_ttr_days}d` : '—', color: 'var(--dark)', sub: '90-day average' },
        ].map((k) => (
          <div key={k.label} style={card}>
            <div style={{ fontFamily: 'var(--fb)', fontSize: 9, fontWeight: 600, letterSpacing: '3px', textTransform: 'uppercase', color: 'var(--mid)', marginBottom: 8 }}>{k.label}</div>
            <div style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 36, fontWeight: 300, color: k.color, lineHeight: 1 }}>{k.value}</div>
            <div style={{ fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--mid)', marginTop: 6 }}>{k.sub}</div>
          </div>
        ))}
      </div>

      {/* Trend chart */}
      <div style={{ ...card, marginBottom: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
          <div>
            <Eyebrow>12-Week Trend</Eyebrow>
            <div style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 20, fontWeight: 300, color: 'var(--dark)' }}>
              Compliance Score History
            </div>
          </div>
          {scores.length > 1 && <SparkLine scores={scores} />}
        </div>
        <div style={{ display: 'flex', gap: 0, borderRadius: 8, overflow: 'hidden', border: '1px solid rgba(0,51,102,0.1)' }}>
          {trends?.trend.slice(-8).map((t, i) => {
            const sc = t.compliance_score;
            const bg = sc >= 90 ? '#E0F7EF' : sc >= 70 ? '#FEF0E6' : '#FDEAEA';
            const fg = sc >= 90 ? '#0D5C3A' : sc >= 70 ? '#7A3800' : '#7A1020';
            return (
              <div key={t.week_start} style={{ flex: 1, background: bg, padding: '12px 8px', textAlign: 'center', borderLeft: i > 0 ? '1px solid rgba(255,255,255,0.5)' : 'none' }}>
                <div style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 20, fontWeight: 300, color: fg }}>{sc}%</div>
                <div style={{ fontFamily: 'var(--fb)', fontSize: 9, color: fg, opacity: 0.7, marginTop: 2 }}>
                  W{i + 1}
                </div>
                {t.new_flagged > 0 && (
                  <div style={{ fontFamily: 'var(--fb)', fontSize: 9, color: '#E03448', marginTop: 2 }}>+{t.new_flagged}</div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Risk exposure */}
      {exposure && (
        <div style={card}>
          <Eyebrow>Regulatory Risk</Eyebrow>
          <div style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 20, fontWeight: 300, color: 'var(--dark)', marginBottom: 20 }}>
            Estimated Fine Exposure
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginBottom: 16 }}>
            {[
              { label: 'Exposed Records', value: exposure.total_exposed_records.toLocaleString(), color: '#E03448' },
              { label: 'Low Estimate', value: `€${exposure.estimated_fine_low_eur.toLocaleString()}`, color: '#F07020' },
              { label: 'High Estimate', value: `€${exposure.estimated_fine_high_eur.toLocaleString()}`, color: '#E03448' },
            ].map((k) => (
              <div key={k.label} style={{ background: 'var(--light)', borderRadius: 10, padding: '16px 20px' }}>
                <div style={{ fontFamily: 'var(--fb)', fontSize: 9, fontWeight: 600, letterSpacing: '2px', textTransform: 'uppercase', color: 'var(--mid)', marginBottom: 6 }}>{k.label}</div>
                <div style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 28, fontWeight: 300, color: k.color }}>{k.value}</div>
              </div>
            ))}
          </div>
          <p style={{ fontFamily: 'var(--fb)', fontSize: 11, color: 'var(--mid)', lineHeight: 1.7 }}>{exposure.methodology}</p>
        </div>
      )}
    </>
  );
}

// ─── Root App ─────────────────────────────────────────────────────────────────

export default function App() {
  const { token, user } = useAuthStore();
  const [page, setPage] = useState<Page>('dashboard');
  const [selectedTableId, setSelectedTableId] = useState<string | null>(null);
  const [sourceFilter, setSourceFilter] = useState<string>('');

  if (!token) {
    return (
      <>
        <style>{CSS_VARS}</style>
        <LoginPage />
      </>
    );
  }

  const handleTableClick = (tableId: string) => {
    setSelectedTableId(tableId);
    setPage('pii-report');
  };

  const handleSourceClick = (source: string) => {
    setSourceFilter(source);
    setPage('dashboard');
  };

  return (
    <>
      <style>{CSS_VARS}</style>
      <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
        <Nav page={page} setPage={(p) => { setPage(p); if (p !== 'pii-report') setSelectedTableId(null); }} />
        <main style={{ flex: 1 }}>
          {page === 'dashboard' && (
            <DashboardPage onTableClick={handleTableClick} />
          )}
          {page === 'pii-report' && selectedTableId && (
            <PIIReportPage
              tableId={selectedTableId}
              userRole={user?.role ?? 'viewer'}
              onBack={() => setPage('dashboard')}
            />
          )}
          {page === 'audit' && <AuditPage />}
          {page === 'data-sources' && <DataSourcesPage onSourceClick={handleSourceClick} />}
          {page === 'compliance' && <CompliancePage />}
          {page === 'info' && <InfoPage />}
        </main>
        <Footer />
      </div>
    </>
  );
}
