import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RequireAuth, RequireRole, RoleGate } from '../auth/guards';
import { ADMIN, MEMBER, renderApp } from '../test/utils';

describe('auth guard', () => {
  it('redirects anonymous users to /login', () => {
    renderApp(<RequireAuth><p>SECRET</p></RequireAuth>, { route: '/app' });
    expect(screen.getByText('LOGIN PAGE')).toBeInTheDocument();
    expect(screen.queryByText('SECRET')).not.toBeInTheDocument();
  });
  it('renders protected content for signed-in users', () => {
    renderApp(<RequireAuth><p>SECRET</p></RequireAuth>, { route: '/app', user: MEMBER });
    expect(screen.getByText('SECRET')).toBeInTheDocument();
  });
});

describe('role gating', () => {
  it('blocks members from admin pages', () => {
    renderApp(<RequireRole role="admin"><p>ADMIN AREA</p></RequireRole>, { user: MEMBER });
    expect(screen.queryByText('ADMIN AREA')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/admins only/i);
  });
  it('lets admins through', () => {
    renderApp(<RequireRole role="admin"><p>ADMIN AREA</p></RequireRole>, { user: ADMIN });
    expect(screen.getByText('ADMIN AREA')).toBeInTheDocument();
  });
  it('RoleGate hides inline admin controls for members', () => {
    renderApp(<><RoleGate role="admin"><button>Danger</button></RoleGate><RoleGate role="member"><span>for all</span></RoleGate></>, { user: MEMBER });
    expect(screen.queryByRole('button', { name: 'Danger' })).not.toBeInTheDocument();
    expect(screen.getByText('for all')).toBeInTheDocument();
  });
});
