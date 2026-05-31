import { useState } from 'react';
import { useAuthStore } from './store/authStore';
import { useLogin } from './hooks/useAuth';
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
`;

type Page = 'dashboard' | 'pii-report' | 'audit' | 'data-sources';

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
              disabled={login.isPending}
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
                cursor: login.isPending ? 'not-allowed' : 'pointer',
                opacity: login.isPending ? 0.7 : 1,
              }}
            >
              {login.isPending ? 'Signing in…' : 'Sign in →'}
            </button>
          </form>
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

  const [modal, setModal] = useState<{ open: boolean; action: RemediateRequest['action'] | null }>({
    open: false,
    action: null,
  });

  const canRemediate = userRole === 'dpo' || userRole === 'admin';

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
        </main>
        <Footer />
      </div>
    </>
  );
}
