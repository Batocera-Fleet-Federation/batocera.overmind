    function setLandingVisible(isVisible) {
      const landing = document.querySelector('.landing-shell');
      const authSection = document.getElementById('auth-section');
      const dashboardSection = document.getElementById('dashboard-section');
      const appShell = document.querySelector('.layout-shell');

      if (landing) {
        landing.style.display = isVisible ? 'block' : 'none';
        landing.toggleAttribute('aria-hidden', !isVisible);
      }
      if (appShell) appShell.style.display = isVisible ? 'none' : 'block';
      if (authSection) {
        authSection.classList.toggle('active', !isVisible);
        authSection.style.display = isVisible ? 'none' : 'block';
      }
      if (dashboardSection) {
        dashboardSection.classList.remove('active');
        dashboardSection.style.display = 'none';
      }
    }

    function showLandingHome() {
      setLandingVisible(true);
      ['login-form', 'register-form', 'verify-form', 'forgot-form', 'reset-form'].forEach((id) => {
        const node = document.getElementById(id);
        if (node) node.style.display = 'none';
      });
    }

    function showLandingAuth(formId) {
      setLandingVisible(false);
      ['login-form', 'register-form', 'verify-form', 'forgot-form', 'reset-form'].forEach((id) => {
        const node = document.getElementById(id);
        if (node) node.style.display = id === formId ? 'block' : 'none';
      });
      const nextHash = formId === 'register-form' ? '#/register' : '#/login';
      if (window.location.hash !== nextHash) history.pushState(null, '', nextHash);
    }

    window.addEventListener('hashchange', () => {
      if (localStorage.getItem('auth_token')) return;
      const hash = window.location.hash || '';
      if (hash === '#/login') {
        showLandingAuth('login-form');
      } else if (hash === '#/register') {
        showLandingAuth('register-form');
      } else if (!hash || hash === '#' || hash === '#/' || hash === '#/home') {
        showLandingHome();
      }
    });
  

                let currentSwarmView = 'drones';
	            let currentUser = null;
	            let currentProfile = null;
	            let currentDevices = [];
	            let currentSwarms = [];
	            let selectedSwarmId = localStorage.getItem('selected_swarm_id') || null;
	            let resetPasswordToken = null;
            let pendingConnections = [];
            let selectedDeviceId = null;
            let currentTab = 'devices';
            let currentDeviceView = 'systems';
            let routeSwarmId = null;
            let currentDeviceSystems = {};
            let deviceRomSearchQuery = '';
            let masterRomPage = 1;
            let biosSearchQuery = '';
            let biosStatusFilter = '';
            let masterBiosPage = 1;
            let artworkSearchQuery = '';
            let artworkStatusFilter = '';
            let artworkTypeFilter = '';
            let artworkSourceDeviceFilter = [];
            let artworkSystemFilter = [];
            let masterArtworkPage = 1;
            let systemPageState = {};
            let pendingConnectionTimer = null;
            let actionRefreshTimer = null;
            let devicesRefreshTimer = null;
            let downloadsRefreshTimer = null;
            let downloadsRefreshInFlight = false;
            let inactivityTimer = null;
            let lastAuthRefreshAt = 0;
            let authRefreshInFlight = false;
            let pendingInvitationToken = sessionStorage.getItem('pending_invitation_token') || null;
            const INACTIVITY_TIMEOUT_MS = 5 * 60 * 1000;
            const AUTH_REFRESH_INTERVAL_MS = 2 * 60 * 1000;
            const MASTER_ROM_PAGE_SIZE = 100;
            const ROMS_PER_PAGE = 20;
            const pageMeta = {
                auth: ['Overlord Login', 'Access the Overmind'],
                devices: ['My Swarm', 'Systems and ROMs'],
                hive: ['The Hive', 'Browse public swarm listings'],
                profile: ['Profile', 'Account, access, and preferences'],
                help: ['Help', 'Install, connect, and configure Drones'],
            };

            document.addEventListener('DOMContentLoaded', async () => {
                    setupInactivityTracking();
	                loadAuthProviders();
	                handleOAuthReturn();
	                handleAuthHashActions();
                const token = localStorage.getItem('auth_token');
                if (token) {
	                    authToken = token;
                    routeSwarmId = parseRoute().swarmId || null;
	                    showDashboard();
	                    await loadSwarms();
	                    loadProfile();
                    loadDevices();
                    loadPendingConnections();
                } else {
                    const hash = window.location.hash || '';
                    const landing = document.querySelector('.landing-shell');
                    const appShell = document.querySelector('.layout-shell');
                    if (hash === '#/login') {
                        hideLandingShell();
                        if (appShell) appShell.style.display = 'block';
                        showAuthOnly('login-form');
                    } else if (hash === '#/register') {
                        hideLandingShell();
                        if (appShell) appShell.style.display = 'block';
                        showAuthOnly('register-form');
                    } else {
                        showLandingShell();
                        if (appShell) appShell.style.display = 'none';
                    }
                }
            });

            window.addEventListener('hashchange', () => {
                markUserActivity();
                if (!authToken) {
                    const hash = window.location.hash || '';
                    if (hash === '#/login') {
                        hideLandingShell();
                        const appShell = document.querySelector('.layout-shell');
                        if (appShell) appShell.style.display = 'block';
                        showAuthOnly('login-form');
                    } else if (hash === '#/register') {
                        hideLandingShell();
                        const appShell = document.querySelector('.layout-shell');
                        if (appShell) appShell.style.display = 'block';
                        showAuthOnly('register-form');
                    } else if (!hash || hash === '#' || hash === '#/' || hash === '#/home') {
                        const landing = document.querySelector('.landing-shell');
                        const appShell = document.querySelector('.layout-shell');
                        const authSection = document.getElementById('auth-section');
                        const dashboardSection = document.getElementById('dashboard-section');
                        showLandingShell();
                        if (appShell) appShell.style.display = 'none';
                        if (authSection) {
                            authSection.classList.remove('active');
                            authSection.style.display = 'none';
                        }
                        if (dashboardSection) {
                            dashboardSection.classList.remove('active');
                            dashboardSection.style.display = 'none';
                        }
                    }
                    return;
                }
                applyRouteFromHash();
            });

            let authToken = localStorage.getItem('auth_token') || null;
            function hideLandingShell() {
                const landing = document.querySelector('.landing-shell');
                if (landing) {
                    landing.style.display = 'none';
                    landing.setAttribute('aria-hidden', 'true');
                }
            }

            function showLandingShell() {
                const landing = document.querySelector('.landing-shell');
                if (landing) {
                    landing.style.display = 'block';
                    landing.removeAttribute('aria-hidden');
                }
            }
	            async function apiGet(path) {
                const response = await fetch(path, {
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (response.status === 401) {
                    logout();
                    showMessage('Session expired. Please log in again.', 'error');
                    throw new Error('Unauthorized');
                }
	                return response;
	            }

	            function withSwarm(path) {
	                if (!selectedSwarmId) return path;
	                const separator = path.includes('?') ? '&' : '?';
	                return `${path}${separator}swarm_id=${encodeURIComponent(selectedSwarmId)}`;
	            }

            async function apiPatch(path, payload) {
                const response = await fetch(path, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${authToken}`
                    },
                    body: JSON.stringify(payload)
                });
                if (response.status === 401) {
                    logout();
                    showMessage('Session expired. Please log in again.', 'error');
                    throw new Error('Unauthorized');
                }
                return response;
            }

            async function apiDelete(path) {
                const response = await fetch(path, {
                    method: 'DELETE',
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (response.status === 401) {
                    logout();
                    showMessage('Session expired. Please log in again.', 'error');
                    throw new Error('Unauthorized');
                }
                return response;
            }

            async function loadAuthProviders() {
                try {
                    const response = await fetch('/api/auth/providers');
                    const data = await response.json();
                    const providers = data.providers || {};
                    ['google', 'github'].forEach(provider => {
                        ['login', 'register'].forEach(form => {
                            const btn = document.getElementById(`${provider}-${form}-btn`);
                            if (!btn) return;
                            btn.disabled = !providers[provider];
                            btn.title = providers[provider]
                                ? `Continue with ${provider}`
                                : `Set ${provider.toUpperCase()}_CLIENT_ID and ${provider.toUpperCase()}_CLIENT_SECRET to enable`;
                        });
                    });
                } catch (error) {
                    console.error('Error loading auth providers:', error);
                }
            }

            function startOAuth(provider) {
                if (pendingInvitationToken) sessionStorage.setItem('pending_invitation_token', pendingInvitationToken);
                window.location.href = `/api/auth/${provider}/start`;
            }

	            function handleOAuthReturn() {
	                const hash = window.location.hash || '';
	                if (!hash.startsWith('#oauth_token=')) return;
                const params = new URLSearchParams(hash.slice(1));
                const token = params.get('oauth_token');
                if (!token) return;
                authToken = token;
                localStorage.setItem('auth_token', authToken);
                pendingInvitationToken = sessionStorage.getItem('pending_invitation_token') || pendingInvitationToken;
	                window.location.hash = '#/devices';
                if (pendingInvitationToken) {
                    acceptPendingInvitation().catch(error => showMessage(error.message, 'error'));
                    return;
                }
	                showMessage('Overlord authenticated.', 'success');
	            }

	            function handleAuthHashActions() {
	                const hash = window.location.hash || '';
	                if (hash.startsWith('#reset-password=')) {
	                    resetPasswordToken = new URLSearchParams(hash.slice(1)).get('reset-password');
	                    showAuthOnly('reset-form');
	                } else if (hash.startsWith('#verified=')) {
	                    showMessage('Email verified. You can log in now.', 'success');
	                } else if (hash.startsWith('#invite=')) {
	                    pendingInvitationToken = new URLSearchParams(hash.slice(1)).get('invite');
                        if (pendingInvitationToken) sessionStorage.setItem('pending_invitation_token', pendingInvitationToken);
	                    handleInvitationLink();
	                }
	            }

            async function handleInvitationLink() {
                if (!pendingInvitationToken) return;
                try {
                    const response = await fetch(`/api/invitations/status?token=${encodeURIComponent(pendingInvitationToken)}`);
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(data.detail || 'Invitation expired or invalid');
                    document.getElementById('login-email').value = data.email || '';
                    document.getElementById('register-email').value = data.email || '';
                    if (authToken) {
                        await acceptPendingInvitation();
                        return;
                    }
                    showAuthOnly(data.registered ? 'login-form' : 'register-form');
                    showMessage(data.registered ? 'Sign in to accept this invitation.' : 'Registration required before accepting this invitation.', 'success');
                } catch (error) {
                    showAuthOnly('login-form');
                    showMessage(error.message, 'error');
                }
            }

            async function acceptPendingInvitation() {
                if (!pendingInvitationToken || !authToken) return;
                const response = await fetch('/api/invitations/accept', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
                    body: JSON.stringify({ token: pendingInvitationToken })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Invitation expired or invalid');
                pendingInvitationToken = null;
                sessionStorage.removeItem('pending_invitation_token');
                window.location.hash = '#/devices';
                await loadSwarms();
                showDashboard();
                renderSwarmSelector();
                await loadDevices();
                showMessage('Invitation accepted.', 'success');
            }

	            function showAuthOnly(id) {
                    hideLandingShell();
                    const appShell = document.querySelector('.layout-shell');
                    if (appShell) appShell.style.display = 'block';
	                ['login-form', 'register-form', 'verify-form', 'forgot-form', 'reset-form'].forEach(formId => {
	                    const node = document.getElementById(formId);
	                    if (node) node.style.display = formId === id ? 'block' : 'none';
	                });
	                document.getElementById('auth-section').style.display = 'block';
	                document.getElementById('dashboard-section').style.display = 'none';
	                setPageChrome('auth');
	            }

	            function showLoginForm() { showAuthOnly('login-form'); }
	            function showForgotPassword() { showAuthOnly('forgot-form'); }

            async function handleLogin(e) {
                e.preventDefault();
                const email = document.getElementById('login-email').value;
                const password = document.getElementById('login-password').value;
                const btn = e.target.querySelector('button');
                btn.disabled = true;
                try {
                    const response = await fetch('/api/auth/login', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email, password })
                    });
	                    if (!response.ok) {
	                        const error = await response.json().catch(() => ({}));
	                        if (response.status === 403) {
	                            document.getElementById('verify-email').value = email;
	                            showAuthOnly('verify-form');
	                        }
	                        throw new Error(error.detail || 'Login failed');
	                    }
	                    const data = await response.json();
	                    authToken = data.access_token;
	                    currentUser = data.user;
	                    currentSwarms = data.swarms || [];
	                    selectedSwarmId = selectedSwarmId || (currentSwarms[0] && currentSwarms[0].id) || null;
	                    if (selectedSwarmId) localStorage.setItem('selected_swarm_id', selectedSwarmId);
	                    localStorage.setItem('auth_token', authToken);
                        if (pendingInvitationToken) {
                            await acceptPendingInvitation();
                            return;
                        }
	                    showDashboard();
	                    renderSwarmSelector();
                    await loadProfile();
                    await loadDevices();
                    await loadPendingConnections();
                    showMessage('Overlord authenticated.', 'success');
                } catch (error) {
                    showMessage('Login failed: ' + error.message, 'error');
                } finally {
                    btn.disabled = false;
                }
            }

            async function handleRegister(e) {
                e.preventDefault();
                const email = document.getElementById('register-email').value;
                const full_name = document.getElementById('register-name').value || null;
                const password = document.getElementById('register-password').value;
                const btn = e.target.querySelector('button');
                btn.disabled = true;
                try {
                    const response = await fetch('/api/auth/register', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email, password, full_name, invitation_token: pendingInvitationToken })
                    });
                    if (!response.ok) {
                        const error = await response.json();
                        throw new Error(error.detail || 'Registration failed');
                    }
                        if (pendingInvitationToken) {
                            pendingInvitationToken = null;
                            sessionStorage.removeItem('pending_invitation_token');
                            showAuthOnly('login-form');
                            document.getElementById('login-email').value = email;
                            showMessage('Registration complete. Sign in to view the swarm.', 'success');
                        } else {
	                        document.getElementById('verify-email').value = email;
	                        showAuthOnly('verify-form');
	                        showMessage('Overlord created. Check your email for the validation code.', 'success');
                        }
                } catch (error) {
                    showMessage('Registration failed: ' + error.message, 'error');
	                } finally {
	                    btn.disabled = false;
	                }
	            }

		            async function handleVerifyEmail(e) {
	                e.preventDefault();
	                const email = document.getElementById('verify-email').value;
	                const code = document.getElementById('verify-code').value;
	                const response = await fetch('/api/auth/verify-email', {
	                    method: 'POST',
	                    headers: { 'Content-Type': 'application/json' },
	                    body: JSON.stringify({ email, code })
	                });
	                if (!response.ok) {
	                    showMessage('Verification failed. Check the code and try again.', 'error');
	                    return;
	                }
	                showLoginForm();
	                showMessage('Email verified. You can log in now.', 'success');
	            }

	            async function handleForgotPassword(e) {
	                e.preventDefault();
	                const email = document.getElementById('forgot-email').value;
	                await fetch('/api/auth/forgot-password', {
	                    method: 'POST',
	                    headers: { 'Content-Type': 'application/json' },
	                    body: JSON.stringify({ email })
	                });
	                showLoginForm();
	                showMessage('If the account is eligible, a reset email has been sent.', 'success');
	            }

	            async function handleResetPassword(e) {
	                e.preventDefault();
	                const password = document.getElementById('reset-password').value;
	                const response = await fetch('/api/auth/reset-password', {
	                    method: 'POST',
	                    headers: { 'Content-Type': 'application/json' },
	                    body: JSON.stringify({ token: resetPasswordToken, password })
	                });
	                if (!response.ok) {
	                    showMessage('Reset link is invalid or expired.', 'error');
	                    return;
	                }
	                window.location.hash = '#/devices';
	                showLoginForm();
	                showMessage('Password updated. You can log in now.', 'success');
	            }

            function setupInactivityTracking() {
                ['mousemove', 'mousedown', 'click', 'keydown', 'touchstart', 'touchmove', 'scroll'].forEach(eventName => {
                    window.addEventListener(eventName, markUserActivity, { passive: true });
                });
            }

            function markUserActivity() {
                if (!authToken) return;
                resetInactivityTimer();
                maybeRefreshAuthToken();
            }

            function resetInactivityTimer() {
                if (inactivityTimer) clearTimeout(inactivityTimer);
                inactivityTimer = setTimeout(() => {
                    logout('You were logged out after 5 minutes of inactivity.', '#/login');
                }, INACTIVITY_TIMEOUT_MS);
            }

            function stopInactivityTimer() {
                if (inactivityTimer) clearTimeout(inactivityTimer);
                inactivityTimer = null;
            }

            async function maybeRefreshAuthToken() {
                const now = Date.now();
                if (authRefreshInFlight || now - lastAuthRefreshAt < AUTH_REFRESH_INTERVAL_MS) return;
                authRefreshInFlight = true;
                try {
                    const response = await fetch('/api/auth/refresh', {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${authToken}` }
                    });
                    if (response.ok) {
                        const data = await response.json();
                        authToken = data.access_token;
                        currentUser = data.user || currentUser;
                        currentSwarms = data.swarms || currentSwarms;
                        localStorage.setItem('auth_token', authToken);
                        lastAuthRefreshAt = now;
                    } else if (response.status === 401) {
                        logout('Session expired. Please log in again.', '#/login');
                    }
                } catch (error) {
                    console.warn('Auth refresh failed; keeping current token until backend validation requires login.');
                } finally {
                    authRefreshInFlight = false;
                }
            }

	            function logout(message = null, nextHash = '#/home') {
                authToken = null;
                currentUser = null;
                currentProfile = null;
                pendingConnections = [];
                selectedDeviceId = null;
                currentDeviceView = 'systems';
                lastAuthRefreshAt = 0;
                if (pendingConnectionTimer) clearInterval(pendingConnectionTimer);
                if (actionRefreshTimer) clearInterval(actionRefreshTimer);
                if (devicesRefreshTimer) clearInterval(devicesRefreshTimer);
                if (downloadsRefreshTimer) clearInterval(downloadsRefreshTimer);
                stopInactivityTimer();
                pendingConnectionTimer = null;
                actionRefreshTimer = null;
                devicesRefreshTimer = null;
                downloadsRefreshTimer = null;
                localStorage.removeItem('auth_token');
                document.body.classList.remove('is-authenticated');
                document.getElementById('auth-section').classList.add('active');
                document.getElementById('dashboard-section').classList.remove('active');
                document.getElementById('auth-section').style.display = 'block';
                document.getElementById('dashboard-section').style.display = 'none';
                document.getElementById('login-form').style.display = 'block';
                document.getElementById('register-form').style.display = 'none';
                setPageChrome('auth');
                if (nextHash === '#/login') {
                    window.location.hash = '#/login';
                    showAuthOnly('login-form');
                } else {
	                window.location.href = '/#/home';
                }
                if (message) showMessage(message, 'error');
	            }

	            function showDashboard() {
                hideLandingShell();
                document.body.classList.add('is-authenticated');
                document.getElementById('auth-section').classList.remove('active');
                document.getElementById('dashboard-section').classList.add('active');
                document.getElementById('auth-section').style.display = 'none';
                document.getElementById('dashboard-section').style.display = 'block';
                setPageChrome(currentTab);
                startPendingConnectionPolling();
                resetInactivityTimer();
            }

            function startPendingConnectionPolling() {
                if (pendingConnectionTimer) clearInterval(pendingConnectionTimer);
                pendingConnectionTimer = setInterval(loadPendingConnections, 10000);
            }

            function startDevicesPolling() {
                if (devicesRefreshTimer) return;
                devicesRefreshTimer = setInterval(() => {
                    // only poll when on devices tab
                    if (currentTab === 'devices' && document.getElementById('devices-tab')?.style.display !== 'none') {
                        loadDevices();
                    }
                }, 30000);
            }

	            function stopDevicesPolling() {
	                if (devicesRefreshTimer) clearInterval(devicesRefreshTimer);
	                devicesRefreshTimer = null;
	            }

	            async function loadSwarms() {
	                if (!authToken) return;
	                const response = await apiGet('/api/swarms');
	                const data = await response.json();
	                currentSwarms = data.swarms || [];
                    const previousSwarmId = selectedSwarmId;
	                if (routeSwarmId && currentSwarms.some(s => s.id === routeSwarmId)) {
	                    selectedSwarmId = routeSwarmId;
	                } else if (!selectedSwarmId || !currentSwarms.some(s => s.id === selectedSwarmId)) {
	                    selectedSwarmId = currentSwarms[0] ? currentSwarms[0].id : null;
	                }
	                if (selectedSwarmId) localStorage.setItem('selected_swarm_id', selectedSwarmId);
	                renderSwarmSelector();
	                renderProfileUI();
	                applyRbacUI();
                    updateSharedSwarmNavButton();
                    if (selectedSwarmId && selectedSwarmId !== previousSwarmId && currentTab === 'devices') {
                        await loadDevices();
                        await loadPendingConnections();
                    }
	            }

	            function renderSwarmSelector() {
	                return;
	            }

	            async function selectSwarm(swarmId, options = {}) {
	                selectedSwarmId = swarmId;
	                localStorage.setItem('selected_swarm_id', selectedSwarmId);
	                selectedDeviceId = null;
                    currentDeviceView = 'systems';
                    if (options.updateRoute !== false) setRoute('devices', null, 'systems', 'drones');
	                renderProfileUI();
                    updateSharedSwarmNavButton();
	                await loadDevices();
	                await loadPendingConnections();
	            }

            function homeSwarmId() {
                const home = currentSwarms.find(s => s.current) || currentSwarms.find(s => s.role === 'overlord') || currentSwarms[0];
                return home ? home.id : null;
            }

            function isSharedSwarmSelected() {
                return !!selectedSwarmId && !!homeSwarmId() && selectedSwarmId !== homeSwarmId();
            }

            function selectedSwarm() {
                return currentSwarms.find(s => s.id === selectedSwarmId) || null;
            }

            function updateSharedSwarmNavButton() {
                const btn = document.getElementById('shared-swarm-nav-btn');
                const label = document.getElementById('shared-swarm-nav-label');
                const mySwarmBtn = document.querySelector('.nav-btn[data-tab="devices"]');
                if (!btn) return;
                const swarm = selectedSwarm();
                const show = currentTab === 'devices' && isSharedSwarmSelected() && swarm;
                btn.style.display = show ? '' : 'none';
                if (label && swarm) label.textContent = swarm.name || 'Shared Swarm';
                btn.classList.toggle('active', !!show);
                if (mySwarmBtn && currentTab === 'devices') mySwarmBtn.classList.toggle('active', !show);
            }

            async function goToMySwarm() {
                const homeId = homeSwarmId();
                if (homeId) {
                    await selectSwarm(homeId);
                } else {
                    selectedDeviceId = null;
                    currentDeviceView = 'systems';
                    setRoute('devices', null, 'systems', 'drones');
                }
                routeSwarmId = null;
                selectedDeviceId = null;
                updateSharedSwarmNavButton();
            }

            function openSelectedSharedSwarm() {
                if (!isSharedSwarmSelected()) {
                    goToMySwarm();
                    return;
                }
                selectedDeviceId = null;
                setRoute('devices', null, 'systems', 'drones');
            }

	            function selectedSwarmRole() {
	                const swarm = currentSwarms.find(s => s.id === selectedSwarmId);
	                return swarm ? swarm.role : null;
	            }

	            function canMutateSwarm() {
	                return selectedSwarmRole() === 'overlord';
	            }

	            function applyRbacUI() {
	                const canMutate = canMutateSwarm();
	                document.querySelectorAll('.mutate-only').forEach(node => {
	                    node.style.display = canMutate ? '' : 'none';
	                });
	                const tokenPanel = document.getElementById('integration-token-panel');
	                if (tokenPanel) tokenPanel.style.display = canMutate && currentSwarmView === 'drones' ? '' : 'none';
	            }

	            function roleLabel(role) {
	                return ({overlord: 'Overlord', overseer: 'Overseer'}[role] || role || 'Member');
	            }

	            async function loadHive() {
	                if (!authToken) return;
                    const container = document.getElementById('hive-list');
                    if (container) container.innerHTML = '<div class="empty-state">Loading swarms...</div>';
	                try {
	                    const response = await apiGet('/api/hive');
	                    if (!response.ok) throw new Error('Failed to load hive');
	                    const data = await response.json();
	                    renderHive(data.hive || []);
	                } catch (error) {
	                    console.error('Error loading hive:', error);
                        if (container) container.innerHTML = '<div class="empty-state">Unable to load The Hive.</div>';
	                }
	            }

	            function renderHive(rows) {
	                const container = document.getElementById('hive-list');
	                if (!container) return;
	                if (!rows.length) {
	                    container.innerHTML = '<div class="empty-state">No swarms registered yet.</div>';
	                    return;
	                }
	                container.innerHTML = rows.map(row => `
	                    <div class="col-12 col-md-6 col-xl-4">
	                    <div class="card device-tile border shadow-sm h-100">
	                        <div class="card-body">
	                            <div class="d-flex align-items-center gap-3 mb-3">
	                                ${row.owner_avatar_data_url ? `<img alt="" src="${escapeHtml(row.owner_avatar_data_url)}" style="width:52px;height:52px;border-radius:50%;object-fit:cover;background:#1f2a44;" onerror="this.style.display='none'">` : `<div aria-hidden="true" style="width:52px;height:52px;border-radius:50%;background:#1f2a44;display:flex;align-items:center;justify-content:center;font-weight:700;">${escapeHtml(String(row.owner_username || 'O').slice(0, 1).toUpperCase())}</div>`}
	                                <div>
	                                    <h5 class="card-title mb-0">${escapeHtml(row.swarm_name || 'Swarm')}</h5>
	                                    <div class="small text-muted">Owner: ${escapeHtml(row.owner_username || 'Overlord')}</div>
	                                </div>
	                            </div>
	                            <div class="small text-muted">Drones: ${escapeHtml(String(row.drone_count || 0))}</div>
	                            <div class="mt-3 d-flex flex-wrap gap-1">
	                                ${row.viewer_role ? `<span class="badge text-bg-primary">${escapeHtml(roleLabel(row.viewer_role))}</span>` : '<span class="badge text-bg-secondary">Public</span>'}
	                                ${row.swarm_id === selectedSwarmId ? '<span class="badge text-bg-success">Selected</span>' : ''}
	                            </div>
                                <button class="btn btn-outline-primary btn-sm mt-3" type="button" ${row.can_view && !row.current ? '' : 'disabled'} onclick="openHiveSwarm('${escapeHtml(row.swarm_id)}')">${row.current ? 'My Swarm' : row.can_view ? 'Open Swarm' : 'Private'}</button>
	                        </div>
	                    </div></div>
	                `).join('');
	            }

            async function openHiveSwarm(swarmId) {
                const swarm = currentSwarms.find(row => row.id === swarmId);
                if (!swarm) {
                    showMessage('This swarm is private.', 'error');
                    return;
                }
                if (swarmId === homeSwarmId()) {
                    showMessage('Use My Swarm to view your own swarm.', 'success');
                    return;
                }
                await selectSwarm(swarmId);
            }

	            function toggleAuthForm() {
	                document.getElementById('verify-form').style.display = 'none';
	                document.getElementById('forgot-form').style.display = 'none';
	                document.getElementById('reset-form').style.display = 'none';
	                document.getElementById('login-form').style.display =
                    document.getElementById('login-form').style.display === 'none' ? 'block' : 'none';
                document.getElementById('register-form').style.display =
                    document.getElementById('register-form').style.display === 'none' ? 'block' : 'none';
            }

            async function loadProfile() {
                try {
                    const response = await apiGet('/api/profile');
                    if (!response.ok) throw new Error('Failed to load profile');
                    currentProfile = await response.json();
                    renderProfileUI();
                } catch (error) {
                    console.error('Error loading profile:', error);
                }
            }

            function renderProfileUI() {
                if (!currentProfile) return;
                const profileLabel = document.getElementById('profile-nav-label');
                if (profileLabel) profileLabel.textContent = currentProfile.username || currentProfile.email || 'Profile';
                const usernameInput = document.getElementById('profile-username-input');
                if (usernameInput) usernameInput.value = currentProfile.username || '';
                const avatarPreview = document.getElementById('profile-avatar-preview');
                if (avatarPreview) avatarPreview.src = currentProfile.avatar_data_url || '';
                const swarm = currentSwarms.find(s => s.id === selectedSwarmId);
                const swarmNameInput = document.getElementById('profile-swarm-name-input');
                if (swarmNameInput) {
                    swarmNameInput.value = swarm ? swarm.name || '' : '';
                    swarmNameInput.disabled = !canMutateSwarm();
                    swarmNameInput.title = canMutateSwarm() ? '' : 'Only the Overlord can rename the swarm.';
                }
                const roleBadge = document.getElementById('access-role-badge');
                if (roleBadge) roleBadge.textContent = roleLabel(selectedSwarmRole());

                const ns = currentProfile.notification_settings || {};
                document.getElementById('notify-slack').checked = !!ns.notify_slack;
                document.getElementById('notify-discord').checked = !!ns.notify_discord;
                document.getElementById('notify-email').checked = !!ns.notify_email;
                document.getElementById('notify-slack-webhook').value = ns.slack_webhook || '';
                document.getElementById('notify-discord-webhook').value = ns.discord_webhook || '';
                document.getElementById('notify-email-address').value = currentProfile.email || '';
                const types = ns.types || {};
                document.getElementById('notify-type-gamelist-update').checked = !!types.gamelist_update;
                document.getElementById('notify-type-device-offline').checked = !!types.device_offline;
                document.getElementById('notify-type-sync-failure').checked = !!types.sync_failure;
                toggleNotificationInputs();
                applyRbacUI();
                loadSwarmAccess();
            }

            function toggleProfilePanel(id) {
                const panel = document.getElementById(id);
                if (panel) panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
            }

            function toggleNotificationInputs() {
                const slackEnabled = document.getElementById('notify-slack').checked;
                const discordEnabled = document.getElementById('notify-discord').checked;
                const emailEnabled = document.getElementById('notify-email').checked;
                document.getElementById('notify-slack-webhook').disabled = !slackEnabled;
                document.getElementById('notify-discord-webhook').disabled = !discordEnabled;
                document.getElementById('notify-email-address').disabled = true;
            }

            async function handleAvatarSelected(event) {
                const file = event.target.files && event.target.files[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = () => showAvatarCropModal(reader.result);
                reader.readAsDataURL(file);
            }

            function showAvatarCropModal(dataUrl) {
                const old = document.getElementById('avatar-crop-overlay');
                if (old) old.remove();
                const overlay = document.createElement('div');
                overlay.id = 'avatar-crop-overlay';
                overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:99999;display:flex;align-items:center;justify-content:center;';
                overlay.innerHTML = `
                    <div class="card" style="max-width:420px;width:92%;background:var(--admin-surface);border-color:var(--admin-border);">
                        <div class="card-body">
                            <h5>Crop Avatar</h5>
                            <canvas id="avatar-crop-canvas" width="320" height="320" style="width:100%;max-width:320px;display:block;margin:0 auto;border-radius:50%;background:#0b1020;"></canvas>
                            <div class="d-flex justify-content-end gap-2 mt-3">
                                <button class="btn btn-outline-secondary" type="button" id="avatar-cancel-btn">Cancel</button>
                                <button class="btn btn-primary" type="button" id="avatar-accept-btn">Use Avatar</button>
                            </div>
                        </div>
                    </div>`;
                document.body.appendChild(overlay);
                const img = new Image();
                img.onload = () => {
                    const canvas = document.getElementById('avatar-crop-canvas');
                    const ctx = canvas.getContext('2d');
                    ctx.clearRect(0, 0, 320, 320);
                    ctx.save();
                    ctx.beginPath();
                    ctx.arc(160, 160, 160, 0, Math.PI * 2);
                    ctx.clip();
                    const size = Math.min(img.width, img.height);
                    const sx = (img.width - size) / 2;
                    const sy = (img.height - size) / 2;
                    ctx.drawImage(img, sx, sy, size, size, 0, 0, 320, 320);
                    ctx.restore();
                };
                img.src = dataUrl;
                document.getElementById('avatar-cancel-btn').onclick = () => overlay.remove();
                document.getElementById('avatar-accept-btn').onclick = () => {
                    const canvas = document.getElementById('avatar-crop-canvas');
                    currentProfile.avatar_data_url = canvas.toDataURL('image/png');
                    renderProfileUI();
                    overlay.remove();
                };
            }

            async function saveProfile(avatarDataUrlOverride = null) {
                try {
                    const payload = {
                        username: document.getElementById('profile-username-input').value.trim() || null,
                    };
                    const avatarValue = avatarDataUrlOverride !== null ? avatarDataUrlOverride : currentProfile.avatar_data_url;
                    if (avatarValue) payload.avatar_data_url = avatarValue;
                    const response = await apiPatch('/api/profile', payload);
                    if (!response.ok) throw new Error('Failed to save profile');
                    currentProfile = await response.json();
                    const swarmName = document.getElementById('profile-swarm-name-input').value.trim();
                    if (selectedSwarmId && canMutateSwarm() && swarmName) {
                        const swarmResponse = await apiPatch(`/api/swarms/${selectedSwarmId}`, { name: swarmName });
                        if (!swarmResponse.ok) throw new Error('Failed to save swarm name');
                        await loadSwarms();
                    }
                    renderProfileUI();
                    showMessage('Profile updated.', 'success');
                } catch (error) {
                    console.error('Error saving profile:', error);
                    showMessage(error.message || 'Profile save failed.', 'error');
                }
            }

            async function loadSwarmAccess() {
                const container = document.getElementById('swarm-access-list');
                if (!container || !selectedSwarmId || !authToken) return;
                try {
                    const response = await apiGet(`/api/swarms/${selectedSwarmId}/access`);
                    if (!response.ok) throw new Error('Failed to load swarm access');
                    const access = (await response.json()).access || {};
                    const members = access.members || [];
                    const invites = access.invitations || [];
                    container.innerHTML = `
                        <div class="table-responsive"><table class="table table-sm align-middle">
                            <thead><tr><th>Email</th><th>Role</th><th>Registration</th><th>Status</th><th></th></tr></thead>
                            <tbody>
                                ${members.map(m => `<tr><td>${escapeHtml(m.email || '')}</td><td>${escapeHtml(roleLabel(m.role))}</td><td>registered</td><td>${escapeHtml(m.status || 'accepted')}</td><td>${canMutateSwarm() && m.role !== 'overlord' ? `<button class="btn btn-outline-danger btn-sm" onclick="removeSwarmMember('${escapeHtml(m.user_id)}')">Remove</button>` : ''}</td></tr>`).join('')}
                                ${invites.map(i => `<tr><td>${escapeHtml(i.email || '')}</td><td>${escapeHtml(roleLabel(i.role))}</td><td>${escapeHtml(i.registration_status || 'invited')}</td><td>${escapeHtml(i.status || 'pending')}</td><td></td></tr>`).join('')}
                            </tbody>
                        </table></div>
                    `;
                } catch (error) {
                    container.innerHTML = '<div class="empty-state">Unable to load swarm access.</div>';
                }
            }

            async function inviteOverseer() {
                const input = document.getElementById('invite-email-input');
                const email = (input.value || '').trim();
                if (!email || !selectedSwarmId) return;
                try {
                    const response = await fetch(`/api/swarms/${selectedSwarmId}/invitations`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}`},
                        body: JSON.stringify({ email })
                    });
                    if (!response.ok) throw new Error('Invite failed');
                    input.value = '';
                    await loadSwarmAccess();
                    showMessage('Overseer invitation sent.', 'success');
                } catch (error) {
                    showMessage(error.message || 'Invite failed.', 'error');
                }
            }

            async function removeSwarmMember(userId) {
                if (!selectedSwarmId || !window.confirm('Remove this user from the swarm?')) return;
                const response = await apiDelete(`/api/swarms/${selectedSwarmId}/members/${userId}`);
                if (!response.ok) {
                    showMessage('Unable to remove user.', 'error');
                    return;
                }
                await loadSwarmAccess();
                showMessage('Swarm access removed.', 'success');
            }

            async function saveNotificationSettings() {
                try {
                    const response = await apiPatch('/api/profile', {
                        notification_settings: {
                            notify_slack: document.getElementById('notify-slack').checked,
                            notify_discord: document.getElementById('notify-discord').checked,
                            notify_email: document.getElementById('notify-email').checked,
                            slack_webhook: document.getElementById('notify-slack-webhook').value.trim(),
                            discord_webhook: document.getElementById('notify-discord-webhook').value.trim(),
                            email_address: currentProfile.email || '',
                            types: {
                                gamelist_update: document.getElementById('notify-type-gamelist-update').checked,
                                device_offline: document.getElementById('notify-type-device-offline').checked,
                                sync_failure: document.getElementById('notify-type-sync-failure').checked
                            }
                        }
                    });
                    if (!response.ok) throw new Error('Failed to save notification settings');
                    currentProfile = await response.json();
                    renderProfileUI();
                    showMessage('Notification settings saved.', 'success');
                } catch (error) {
                    console.error('Error saving notification settings:', error);
                }
            }

            function setSwarmView(view) {
                if (!view || !['drones', 'downloads', 'sync-activity', 'master-list'].includes(view)) {
                    view = 'drones';
                }

                currentSwarmView = view;

                document.querySelectorAll('.swarm-view-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.swarmView === view);
                });

                const isDronesView = view === 'drones';

                const pendingConnections = document.getElementById('pending-connections');
                const integrationTokenPanel = document.getElementById('integration-token-panel');
                const devicesList = document.getElementById('devices-list');
                const swarmGlobalPanel = document.getElementById('swarm-global-panel');

                if (pendingConnections) pendingConnections.style.display = isDronesView ? '' : 'none';
                if (integrationTokenPanel) integrationTokenPanel.style.display = isDronesView ? '' : 'none';
                if (devicesList) devicesList.style.display = isDronesView ? '' : 'none';
                if (swarmGlobalPanel) swarmGlobalPanel.style.display = isDronesView ? 'none' : '';

                if (view === 'drones') {
                    loadDevices();
                    return;
                }

                if (view === 'downloads') {
                    loadSwarmDownloads();
                    return;
                }

                if (view === 'sync-activity') {
                    loadSwarmSyncActivity();
                    return;
                }

                if (view === 'master-list') {
                    loadSwarmMasterList();
                }
            }

            async function loadDevices() {
                try {
	                    const response = await apiGet(withSwarm('/api/devices'));
                    if (!response.ok) throw new Error('Failed to load devices');
                    const data = await response.json();
                    currentDevices = data.devices;
                    if (selectedDeviceId && !currentDevices.some(d => d.device_id === selectedDeviceId)) selectedDeviceId = null;
                    displayDevices();
                    updateSelectedDeviceSummary();
                    updateSelectedDeviceWorkspace();
                    applyRouteFromHash();
                } catch (error) {
                    console.error('Error loading devices:', error);
                }
            }

            async function loadPendingConnections() {
                if (!authToken) return;
                try {
	                    const response = await apiGet(withSwarm('/api/drone-connections'));
                    if (!response.ok) throw new Error('Failed to load drone connections');
                    const data = await response.json();
                    pendingConnections = data.connections || [];
                    displayPendingConnections();
                } catch (error) {
                    console.error('Error loading drone connections:', error);
                }
            }

            function displayPendingConnections() {
                const container = document.getElementById('pending-connections');
                if (!container) return;
                if (!pendingConnections.length) {
                    container.innerHTML = '';
                    return;
                }
                if (!canMutateSwarm()) {
                    container.innerHTML = '';
                    return;
                }
                container.innerHTML = `
                    <div class="connection-panel">
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
                            <div>
                                <div class="fw-bold"><i class="bi bi-broadcast-pin me-1"></i>Psionic connection detected</div>
                                <div class="small text-muted">A Drone is requesting control from the Overmind.</div>
                            </div>
                            <button class="btn btn-outline-secondary btn-sm" onclick="loadPendingConnections()"><i class="bi bi-arrow-repeat me-1"></i>Refresh</button>
                        </div>
                        ${pendingConnections.map(conn => `
                            <div class="card mb-2">
                                <div class="card-body py-2">
                                    <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                                        <div>
                                            <strong>${conn.device_name}</strong>
                                            <div class="small text-muted">Drone ID: <code>${conn.device_id}</code></div>
                                            <div class="small text-muted">Reachable URL: ${escapeHtml((conn.batocera_info || {}).reachable_url || 'n/a')}</div>
                                            <div class="small text-muted">IP: ${escapeHtml((conn.batocera_info || {}).ip_address || 'n/a')}</div>
                                            <div class="small text-muted">Detected: ${conn.detected_at ? new Date(conn.detected_at).toLocaleString() : 'now'}</div>
                                            <div class="small text-muted">Last heartbeat: ${conn.last_seen ? new Date(conn.last_seen).toLocaleString() : 'n/a'}</div>
                                        </div>
                                        <div class="d-flex gap-2">
                                            <button class="btn btn-primary btn-sm" onclick="acceptDroneConnection('${conn.device_id}')"><i class="bi bi-check2-circle me-1"></i>Accept</button>
                                            <button class="btn btn-outline-danger btn-sm" onclick="denyDroneConnection('${conn.device_id}')"><i class="bi bi-x-circle me-1"></i>Deny</button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            }

            async function acceptDroneConnection(deviceId) {
                try {
                    const response = await fetch(`/api/drone-connections/${deviceId}/accept`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${authToken}` }
                    });
                    if (!response.ok) throw new Error('Failed to accept Drone connection');
                    await loadPendingConnections();
                    await loadDevices();
                    showMessage('Drone registered to the Overlord.', 'success');
                } catch (error) {
                    console.error('Error accepting Drone connection:', error);
                }
            }

            async function denyDroneConnection(deviceId) {
                if (!window.confirm('Deny this psionic connection?')) return;
                try {
                    const response = await fetch(`/api/drone-connections/${deviceId}/deny`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${authToken}` }
                    });
                    if (!response.ok) throw new Error('Failed to deny Drone connection');
                    await loadPendingConnections();
                    showMessage('Drone connection denied.', 'success');
                } catch (error) {
                    console.error('Error denying Drone connection:', error);
                }
            }

            async function generateIntegrationToken() {
                try {
                    const response = await fetch('/api/integration-tokens', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify({ label: 'Local Drone onboarding' })
                    });
                    if (!response.ok) throw new Error('Failed to generate authorization token');
                    const data = await response.json();
                    showTokenModal(data.token.authorization_token);
                } catch (error) {
                    console.error('Error generating integration token:', error);
                }
            }

            function updateSelectedDeviceSummary() {
                const summary = document.getElementById('selected-device-summary');
                if (!summary) return;
                if (currentTab !== 'devices') {
                    summary.style.display = 'none';
                    summary.textContent = '';
                    return;
                }
                summary.style.display = selectedDeviceId ? 'none' : 'block';
                if (!selectedDeviceId) {
                    summary.textContent = 'Select a Drone to view systems, ROMs, and logs.';
                    return;
                }
                const device = currentDevices.find(d => d.device_id === selectedDeviceId);
                summary.textContent = device ? `Selected Drone: ${device.device_name} (${device.device_id})` : `Selected Drone: ${selectedDeviceId}`;
            }

            function displayDevices() {
                const container = document.getElementById('devices-list');
                const globalPanel = document.getElementById('swarm-global-panel');
                if (globalPanel && globalPanel.style.display !== 'none') return;
                if (currentDevices.length === 0) {
                    container.innerHTML = '<div class="empty-state">No Drones registered yet</div>';
                    return;
                }
                container.innerHTML = `
                    <div class="device-grid">
                        ${currentDevices.map(device => `
                            <button type="button" class="card device-tile text-start border shadow-sm ${device.device_id === selectedDeviceId ? 'active' : ''}" onclick="selectDevice('${device.device_id}')">
                                <div class="card-body">
                                    <div class="d-flex align-items-start justify-content-between gap-2 mb-2">
                                        <h5 class="card-title mb-0">${device.device_name}</h5>
                                        <i class="bi bi-hdd-network text-muted"></i>
                                    </div>
                                    <div class="small text-muted mb-3">Drone ID</div>
                                    <code class="small d-block text-break">${device.device_id}</code>
                                    <div class="mt-3 d-flex flex-wrap gap-1">
                                        <span class="badge ${device.online ? 'text-bg-success' : 'text-bg-danger'}">${device.online ? 'Online' : 'Offline'}</span>
                                        <span class="badge ${device.swarm_connected ? 'text-bg-success' : 'text-bg-secondary'}">${device.swarm_connected ? 'Connected to Swarm' : 'Not Connected to Swarm'}</span>
                                    </div>
                                    <div class="small text-muted mt-3">${device.last_seen ? `Last seen: ${new Date(device.last_seen).toLocaleString()}` : 'Last seen unavailable'}</div>
                                </div>
                            </button>
                        `).join('')}
                    </div>
                `;
            }

            function setActiveSwarmView(viewName) {
                currentSwarmView = viewName;
                if (viewName !== 'downloads') stopSwarmDownloadsAutoRefresh();
                document.querySelectorAll('.swarm-view-btn').forEach(btn => {
                    const active = btn.getAttribute('data-swarm-view') === viewName;
                    btn.classList.toggle('active', active);
                    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
                });
                const pendingPanel = document.getElementById('pending-connections');
                if (pendingPanel) pendingPanel.style.display = viewName === 'drones' ? '' : 'none';
                applyRbacUI();
            }

            function stopSwarmDownloadsAutoRefresh() {
                if (downloadsRefreshTimer) clearInterval(downloadsRefreshTimer);
                downloadsRefreshTimer = null;
                downloadsRefreshInFlight = false;
            }

            function startSwarmDownloadsAutoRefresh() {
                if (downloadsRefreshTimer) return;
                downloadsRefreshTimer = setInterval(async () => {
                    if (currentTab !== 'devices' || currentSwarmView !== 'downloads') {
                        stopSwarmDownloadsAutoRefresh();
                        return;
                    }
                    if (downloadsRefreshInFlight) return;
                    downloadsRefreshInFlight = true;
                    try {
                        await showSwarmDownloads(false, {quiet: true});
                    } finally {
                        downloadsRefreshInFlight = false;
                    }
                }, 5000);
            }

            function setSwarmView(viewName) {
                selectedDeviceId = null;
                currentDeviceView = 'systems';
                setRoute('devices', null, 'systems', viewName);
            }

            function showSwarmHome(updateUrl = true) {
                if (updateUrl) {
                    setSwarmView('drones');
                    return;
                }
                setActiveSwarmView('drones');
                const panel = document.getElementById('swarm-global-panel');
                const list = document.getElementById('devices-list');
                if (panel) {
                    panel.style.display = 'none';
                    panel.innerHTML = '';
                }
                if (list) list.style.display = 'block';
                displayDevices();
            }

            function formatDuration(row) {
                const ms = Number(row.duration_ms ?? '');
                if (Number.isFinite(ms) && ms >= 0) {
                    return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
                }
                const seconds = Number(row.duration_seconds ?? '');
                if (Number.isFinite(seconds) && seconds >= 0) return `${seconds.toFixed(1)}s`;
                return '';
            }

            function formatBytes(value) {
                const n = Number(value || 0);
                if (!Number.isFinite(n) || n <= 0) return '0 B';
                const units = ['B', 'KB', 'MB', 'GB', 'TB'];
                let size = n;
                let unit = 0;
                while (size >= 1024 && unit < units.length - 1) {
                    size /= 1024;
                    unit += 1;
                }
                return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
            }

            function flattenDownloadTargets(targets) {
                const rows = [];
                (targets || []).forEach(target => {
                    [...(target.active || []), ...(target.queued || [])].forEach(row => {
                        rows.push({
                            ...row,
                            target_drone_id: row.target_drone_id || target.target_drone_id,
                            target_device_name: target.device_name || target.target_drone_id,
                        });
                    });
                });
                return rows;
            }

            function renderSwarmDownloadsTable(rows) {
                return `<div class="table-responsive"><table class="table table-sm align-middle">
                    <thead><tr>
                        <th>Target</th><th>Source</th><th>Status</th><th>Queue</th><th>File</th><th>Size</th><th>Downloaded</th><th>Progress</th><th>Speed</th><th>Started</th><th>Reason</th><th></th>
                    </tr></thead>
                    <tbody>${rows.map(row => {
                        const pct = Number(row.percentage || 0);
                        const active = ['queued', 'downloading'].includes(String(row.status || ''));
                        const canCancel = active && canMutateSwarm();
                        const fileLabel = [row.file_path || row.relative_path || row.rom_path || row.rom_name || '', row.artwork_type || ''].filter(Boolean).join(' / ');
                        return `<tr>
                            <td class="small"><div class="fw-semibold">${escapeHtml(row.target_device_name || row.target_drone_id || 'n/a')}</div><div class="small text-muted mono">${escapeHtml(row.target_drone_id || '')}</div></td>
                            <td class="small mono">${escapeHtml(row.source_drone_id || 'n/a')}</td>
                            <td><span class="badge text-bg-${row.status === 'failed' ? 'danger' : row.status === 'completed' ? 'success' : row.status === 'cancelled' ? 'secondary' : 'primary'}">${escapeHtml(row.status || 'queued')}</span></td>
                            <td class="small">${row.queue_position ? `#${escapeHtml(row.queue_position)}` : row.status === 'downloading' ? 'active' : ''}</td>
                            <td class="small">${escapeHtml(fileLabel)}${row.asset_type ? `<div class="small text-muted">${escapeHtml(row.asset_type)}</div>` : ''}${row.failure_reason || row.error_message ? `<div class="text-danger">${escapeHtml(row.failure_reason || row.error_message)}</div>` : ''}</td>
                            <td class="small">${formatBytes(row.total_bytes || row.file_size)}</td>
                            <td class="small">${formatBytes(row.downloaded_bytes || row.bytes_transferred)}</td>
                            <td style="min-width:150px"><div class="progress" style="height:.55rem"><div class="progress-bar" style="width:${Math.max(0, Math.min(100, pct))}%"></div></div><div class="small text-muted">${pct.toFixed(1)}%</div></td>
                            <td class="small">${row.transfer_speed_bps ? `${formatBytes(row.transfer_speed_bps)}/s` : ''}</td>
                            <td class="small text-muted">${escapeHtml(row.started_at || row.download_started_at || row.created_at || '')}</td>
                            <td class="small text-danger">${escapeHtml(row.failure_reason || row.error_message || row.cancel_reason || '')}</td>
                            <td>${canCancel ? `<button class="btn btn-outline-danger btn-sm" onclick="cancelSwarmDownload('${escapeHtml(row.target_drone_id)}','${escapeHtml(row.job_id || row.id)}')"><i class="bi bi-x-circle"></i></button>` : ''}</td>
                        </tr>`;
                    }).join('')}</tbody></table></div>`;
            }

            async function showSwarmDownloads(updateUrl = true, options = {}) {
                if (updateUrl) {
                    setSwarmView('downloads');
                    return;
                }
                if (currentTab !== 'devices') {
                    stopSwarmDownloadsAutoRefresh();
                    return;
                }
                setActiveSwarmView('downloads');
                const panel = document.getElementById('swarm-global-panel');
                const list = document.getElementById('devices-list');
                if (!panel) return;
                if (list) list.style.display = 'none';
                panel.style.display = 'block';
                if (!options.quiet) panel.innerHTML = `<div class="card"><div class="card-body py-2">Loading downloads...</div></div>`;
                try {
                    const response = await apiGet('/api/downloads');
                    if (!response.ok) throw new Error('Failed to load downloads');
                    const payload = await response.json();
                    const targets = payload.targets || [];
                    const rows = flattenDownloadTargets(targets);
                    if (!rows.length) {
                        panel.innerHTML = '<div class="empty-state">No downloads in flight.</div>';
                        startSwarmDownloadsAutoRefresh();
                        return;
                    }
                    panel.innerHTML = `<div class="card"><div class="card-body py-3">
                        <div class="d-flex flex-wrap justify-content-between gap-2 mb-3">
                            <div><strong>Swarm Downloads</strong><div class="small text-muted">Concurrency is limited to one active download per target Drone, not globally.</div></div>
                            <button class="btn btn-outline-primary btn-sm" onclick="showSwarmDownloads(false)"><i class="bi bi-arrow-repeat"></i></button>
                        </div>
                        ${renderSwarmDownloadsTable(rows)}
                    </div></div>`;
                    applyRbacUI();
                    startSwarmDownloadsAutoRefresh();
                } catch (error) {
                    if (!options.quiet) panel.innerHTML = '<div class="empty-state">Unable to load downloads.</div>';
                }
            }

            async function cancelSwarmDownload(deviceId, jobId) {
                if (!deviceId || !jobId || !window.confirm('Cancel this download on the target Drone?')) return;
                const response = await fetch(`/api/devices/${encodeURIComponent(deviceId)}/downloads/${encodeURIComponent(jobId)}/cancel`, {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (!response.ok) {
                    showMessage('Unable to queue cancel request.', 'error');
                    return;
                }
                showMessage('Cancel request sent to the target Drone.', 'success');
                await showSwarmDownloads(false);
            }

            async function showSwarmSyncActivity(updateUrl = true) {
                if (updateUrl) {
                    setSwarmView('sync-activity');
                    return;
                }
                setActiveSwarmView('sync-activity');
                const panel = document.getElementById('swarm-global-panel');
                const list = document.getElementById('devices-list');
                if (!panel) return;
                if (list) list.style.display = 'none';
                panel.style.display = 'block';
                const q = document.getElementById('swarm-sync-search')?.value || '';
                const statusValue = document.getElementById('swarm-sync-status')?.value || '';
                const params = new URLSearchParams();
                if (q.trim()) params.set('q', q.trim());
                if (statusValue) params.set('status_filter', statusValue);
                panel.innerHTML = `<div class="card"><div class="card-body py-2">Loading swarm sync activity...</div></div>`;
                try {
                    const response = await apiGet('/api/sync-activity' + (params.toString() ? `?${params.toString()}` : ''));
                    if (!response.ok) throw new Error('Failed to load sync activity');
                    const rows = (await response.json()).activity || [];
                    panel.innerHTML = `<div class="card"><div class="card-body py-2">
                        <div class="d-flex flex-wrap align-items-end gap-2 mb-3">
                            <div style="flex:1;min-width:240px">
                                <label class="form-label" for="swarm-sync-search">Search Sync Activity</label>
                                <input id="swarm-sync-search" class="form-control" type="search" value="${escapeHtml(q)}" placeholder="Drone, file, md5, status, date, error">
                            </div>
                            <div style="min-width:160px">
                                <label class="form-label" for="swarm-sync-status">Status</label>
                                <select id="swarm-sync-status" class="form-select">
                                    <option value="">All</option>
                                    <option value="pending" ${statusValue === 'pending' ? 'selected' : ''}>Pending</option>
                                    <option value="completed" ${statusValue === 'completed' ? 'selected' : ''}>Completed</option>
                                    <option value="failed" ${statusValue === 'failed' ? 'selected' : ''}>Failed</option>
                                    <option value="skipped" ${statusValue === 'skipped' ? 'selected' : ''}>Skipped</option>
                                </select>
                            </div>
                            <button class="btn btn-primary" onclick="showSwarmSyncActivity(false)">Search</button>
                        </div>
                        <div class="table-responsive"><table class="table table-sm align-middle"><thead><tr>
                            <th>Status</th><th>Transfer</th><th>Asset</th><th>MD5</th><th>Duration</th><th>Time</th>
                        </tr></thead><tbody>
                            ${rows.map(row => {
                                const duration = formatDuration(row);
                                const statusClass = row.status === 'completed' ? 'text-bg-success' : row.status === 'failed' ? 'text-bg-danger' : 'text-bg-secondary';
                                const assetParts = [row.asset_type === 'bios' ? 'bios' : (row.system || ''), row.relative_path || row.rom_path || row.rom_name || row.bios_name || ''];
                                if (row.artwork_type) assetParts.push(row.artwork_type);
                                return `<tr>
                                    <td><span class="badge ${statusClass}">${escapeHtml(row.status || 'pending')}</span></td>
                                    <td class="small">${escapeHtml(row.source_drone_id || 'source n/a')} &rarr; ${escapeHtml(row.target_drone_id || 'target n/a')}</td>
                                    <td class="small">${escapeHtml(assetParts.filter(Boolean).join(' / '))}${row.failure_reason ? `<div class="text-danger">${escapeHtml(row.failure_reason)}</div>` : ''}</td>
                                    <td class="small mono">${escapeHtml(row.rom_md5 || row.bios_md5 || row.md5 || '')}</td>
                                    <td class="small">${duration ? escapeHtml(row.status === 'failed' ? `Failed after ${duration}` : row.status === 'completed' ? `Completed in ${duration}` : duration) : ''}</td>
                                    <td class="small text-muted">${escapeHtml(row.completed_at || row.started_at || row.received_at || '')}</td>
                                </tr>`;
                            }).join('')}
                        </tbody></table></div>
                        ${rows.length ? '' : '<div class="small text-muted">No swarm sync activity matched.</div>'}
                    </div></div>`;
                    document.getElementById('swarm-sync-search')?.addEventListener('keydown', event => {
                        if (event.key === 'Enter') showSwarmSyncActivity(false);
                    });
                } catch (error) {
                    panel.innerHTML = '<div class="empty-state">Unable to load swarm sync activity.</div>';
                }
            }

            async function showSwarmMasterList(updateUrl = true) {
                if (updateUrl) {
                    setSwarmView('master-list');
                    return;
                }
                setActiveSwarmView('master-list');
                const panel = document.getElementById('swarm-global-panel');
                const list = document.getElementById('devices-list');
                if (!panel) return;
                if (list) list.style.display = 'none';
                panel.style.display = 'block';
                const q = document.getElementById('swarm-master-search')?.value || '';
                const params = new URLSearchParams();
                if (q.trim()) params.set('q', q.trim());
                params.set('per_page', '250');
                panel.innerHTML = `<div class="card"><div class="card-body py-2">Loading swarm master list...</div></div>`;
                try {
                    if (!currentDevices.length) await loadDevices();
                    const response = await apiGet('/api/master-roms' + (params.toString() ? `?${params.toString()}` : ''));
                    if (!response.ok) throw new Error('Failed to load master list');
                    const payload = await response.json();
                    const rows = payload.roms || [];
                    const systemsResp = await apiGet('/api/systems');
                    const systemsPayload = systemsResp.ok ? await systemsResp.json() : {systems: []};
                    const systemOptions = (systemsPayload.systems || [])
                        .map(row => row.system_name)
                        .filter(Boolean)
                        .sort((a, b) => a.localeCompare(b));
                    const bulkDevices = currentDevices
                        .filter(device => device && device.device_id)
                        .sort((a, b) => String(a.device_name || a.device_id).localeCompare(String(b.device_name || b.device_id)));
                    panel.innerHTML = `<div class="card"><div class="card-body py-2">
                        <div class="d-flex flex-wrap align-items-end gap-2 mb-3">
                            <div style="flex:1;min-width:240px">
                                <label class="form-label" for="swarm-master-search">Search Master List</label>
                                <input id="swarm-master-search" class="form-control" type="search" value="${escapeHtml(q)}" placeholder="System, ROM, md5, Drone">
                            </div>
                            <button class="btn btn-primary" onclick="showSwarmMasterList(false)">Search</button>
                        </div>
                        <div class="border rounded p-3 mb-3" style="border-color:var(--admin-border)!important;background:rgba(31,42,68,.35)">
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <div>
                                    <strong>Bulk Sync</strong>
                                    <div class="small text-muted">Select two or more Drones and one or more systems. Each selected Drone will receive ROMs it is missing from the selected group.</div>
                                </div>
                                <button class="btn btn-outline-primary btn-sm" onclick="queueBulkSwarmSync()">Queue Bulk Sync</button>
                            </div>
                            <div class="row g-3">
                                <div class="col-12 col-lg-6">
                                    <div class="small text-muted mb-1">Drones</div>
                                    <div class="d-flex flex-wrap gap-2">
                                        ${bulkDevices.map(device => `
                                            <label class="form-check form-check-inline mb-0">
                                                <input class="form-check-input bulk-sync-drone" type="checkbox" value="${escapeHtml(device.device_id)}">
                                                <span class="form-check-label">${escapeHtml(device.device_name || device.device_id)}</span>
                                            </label>
                                        `).join('') || '<span class="small text-muted">No Drones available.</span>'}
                                    </div>
                                </div>
                                <div class="col-12 col-lg-6">
                                    <div class="small text-muted mb-1">Systems</div>
                                    <div class="d-flex flex-wrap gap-2">
                                        ${systemOptions.map(system => `
                                            <label class="form-check form-check-inline mb-0">
                                                <input class="form-check-input bulk-sync-system" type="checkbox" value="${escapeHtml(system)}">
                                                <span class="form-check-label">${escapeHtml(system)}</span>
                                            </label>
                                        `).join('') || '<span class="small text-muted">No systems reported yet.</span>'}
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="small text-muted mb-2">${payload.total || rows.length} unique ROMs across approved Drones</div>
                        <div class="table-responsive"><table class="table table-sm align-middle"><thead><tr>
                            <th>System</th><th>ROM</th><th>Size</th><th>Drones</th>
                        </tr></thead><tbody>
                            ${rows.map(row => {
                                const devices = (row.devices || []).map(d => d.device_name || d.device_id).join(', ');
                                const filenames = (row.filenames || []).length > 1 ? `<div class="small text-muted">${(row.filenames || []).map(escapeHtml).join('<br>')}</div>` : '';
                                const sizeText = row.file_size ? `${(Number(row.file_size) / 1024 / 1024).toFixed(2)} MB` : '';
                                return `<tr>
                                    <td>${escapeHtml(row.system_name || '')}</td>
                                    <td>${escapeHtml(row.rom_name || row.file_path || '')}${row.rom_md5 ? `<div class="small fst-italic text-muted mono">md5: ${escapeHtml(row.rom_md5)}</div>` : ''}${filenames}</td>
                                    <td class="small text-muted">${escapeHtml(sizeText)}</td>
                                    <td class="small">${escapeHtml(devices)}</td>
                                </tr>`;
                            }).join('')}
                        </tbody></table></div>
                        ${rows.length ? '' : '<div class="small text-muted">No ROMs matched.</div>'}
                    </div></div>`;
                    document.getElementById('swarm-master-search')?.addEventListener('keydown', event => {
                        if (event.key === 'Enter') showSwarmMasterList(false);
                    });
                } catch (error) {
                    panel.innerHTML = '<div class="empty-state">Unable to load swarm master list.</div>';
                }
            }

            async function queueBulkSwarmSync() {
                const deviceIds = Array.from(document.querySelectorAll('.bulk-sync-drone:checked')).map(input => input.value);
                const systems = Array.from(document.querySelectorAll('.bulk-sync-system:checked')).map(input => input.value);
                if (deviceIds.length < 2) {
                    showMessage('Select at least two Drones for bulk sync.', 'error');
                    return;
                }
                if (!systems.length) {
                    showMessage('Select at least one system for bulk sync.', 'error');
                    return;
                }
                try {
                    const response = await fetch('/api/bulk-sync', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}`},
                        body: JSON.stringify({device_ids: deviceIds, systems})
                    });
                    if (!response.ok) throw new Error('Failed to queue bulk sync');
                    const payload = await response.json();
                    showMessage(`Bulk sync queued: ${payload.action_count || 0} action(s), ${payload.queued_rom_count || 0} ROM transfer(s).`, 'success');
                } catch (error) {
                    console.error('Error queuing bulk sync:', error);
                    showMessage('Failed to queue bulk sync.', 'error');
                }
            }

            function selectDevice(deviceId) {
                selectedDeviceId = deviceId;
                currentDeviceView = 'systems';
                currentDeviceSystems = {};
                systemPageState = {};
                deviceRomSearchQuery = '';
                displayDevices();
                updateSelectedDeviceSummary();
                updateSelectedDeviceWorkspace();
                switchTab('devices', null, false);
                switchDeviceView('systems', null, false);
                setRoute('devices', deviceId, 'systems');
            }

            async function loadGameLogs(options = {}) {
                if (!selectedDeviceId) {
                    document.getElementById('gamelogs-list').innerHTML = '<div class="empty-state">Select a Drone to view logs.</div>';
                    return;
                }
                if (options.queue) {
                    await requestDeviceDataSnapshot('logs');
                    return;
                }
                try {
                    const [logsResp, deviceResp] = await Promise.all([
                        apiGet(`/api/devices/${selectedDeviceId}/gamelogs`),
                        apiGet(`/api/devices/${selectedDeviceId}`)
                    ]);
                    if (!logsResp.ok) throw new Error('Failed to load game logs');
                    if (!deviceResp.ok) throw new Error('Failed to load device details');
                    const logsData = await logsResp.json();
                    const deviceData = await deviceResp.json();
                    const snapshotLogs = deviceData.game_logs && Array.isArray(deviceData.game_logs.sessions) ? deviceData.game_logs.sessions : [];
                    displayCombinedLogs({
                        gamelogs: snapshotLogs.length ? snapshotLogs : (logsData.gamelogs || []),
                        emulator_configs: deviceData.emulator_configs || [],
                        log_sources: deviceData.log_sources || []
                    });
                } catch (error) {
                    console.error('Error loading logs:', error);
                }
            }

            function displayCombinedLogs({gamelogs, emulator_configs, log_sources}) {
                const container = document.getElementById('gamelogs-list');
                const sources = [];
                const logPayload = log_sources && !Array.isArray(log_sources) ? log_sources : {};
                const sourceRows = Array.isArray(logPayload.logs) ? logPayload.logs : (Array.isArray(log_sources) ? log_sources : []);
                sourceRows.forEach(row => {
                    const label = row.source || row.name || row.path || 'log_source';
                    const content = (row.files || []).map(file => {
                        if (typeof file === 'string') return file;
                        return file.content || file.path || JSON.stringify(file, null, 2);
                    }).join('\\n\\n');
                    sources.push({id: label, label: label.replaceAll('_', ' '), path: (row.files || []).map(f => f.path || f.name).filter(Boolean).join(', '), content});
                });
                const gameLines = (Array.isArray(gamelogs) ? gamelogs : []).map(log => {
                    const when = log.played_at ? new Date(log.played_at).toLocaleString() : '';
                    return `${when} ${log.system_name || ''} ${log.game_name || ''}`.trim();
                });
                sources.unshift({id: 'game_logs', label: 'Game Logs', path: 'Overmind gameplay history', content: gameLines.join('\\n') || 'No game logs reported yet.'});
                const first = sources[0];
                container.innerHTML = `
                    <div class="row">
                        <div class="col-md-3 mb-3">
                                <div class="card log-card">
                                    <div class="card-header">Log Sources</div>
                                    <div class="list-group list-group-flush source-selector" id="overmindLogSources">
                                        ${sources.map((source, index) => `
                                        <button type="button" class="list-group-item list-group-item-action text-start" onclick="selectOvermindLogSource(${index})">
                                            <i class="bi bi-journal-text me-2"></i>${escapeHtml(source.label)}
                                        </button>
                                    `).join('')}
                                </div>
                            </div>
                        </div>
                        <div class="col-md-9">
                            <div class="card log-card">
                                <div class="card-header d-flex flex-wrap justify-content-between align-items-center gap-2">
                                    <span id="overmindLogTitle">Select a log source</span>
                                    <button class="btn btn-sm btn-outline-primary" onclick="loadGameLogs({queue:true})">Refresh</button>
                                </div>
                                <div class="card-body">
                                    <div id="overmindLogPath" class="small text-muted mb-2"></div>
                                    <pre id="overmindLogContent" class="mono bg-dark text-light p-3" style="max-height:600px;overflow:auto;white-space:pre-wrap;">Select a source from the left panel to view logs.</pre>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
                window.overmindLogSources = sources;
                if (sources.length) {
                    setTimeout(() => selectOvermindLogSource(0), 0);
                }
            }

            function selectOvermindLogSource(index) {
                const sources = window.overmindLogSources || [];
                const source = sources[index];
                if (!source) return;
                document.querySelectorAll('#overmindLogSources .list-group-item').forEach((node, idx) => node.classList.toggle('active', idx === index));
                const title = document.getElementById('overmindLogTitle');
                const path = document.getElementById('overmindLogPath');
                const content = document.getElementById('overmindLogContent');
                if (title) title.textContent = source.label;
                if (path) path.textContent = source.path || '';
                if (content) content.textContent = source.content || '';
            }

            async function loadDeviceConfigs(options = {}) {
                const container = document.getElementById('configs-list');
                if (!selectedDeviceId || !container) return;
                if (options.queue) {
                    await requestDeviceDataSnapshot('configs');
                    return;
                }
                try {
                    const response = await apiGet(`/api/devices/${selectedDeviceId}`);
                    if (!response.ok) throw new Error('Failed to load config data');
                    const device = await response.json();
                    displayDeviceConfigs(device.emulator_configs || null);
                } catch (error) {
                    container.innerHTML = '<div class="empty-state">Unable to load emulator configs.</div>';
                }
            }

            function displayDeviceConfigs(configPayload) {
                const container = document.getElementById('configs-list');
                const configs = configPayload && Array.isArray(configPayload.configs) ? configPayload.configs : [];
                if (!configs.length) {
                    container.innerHTML = `<div class="card"><div class="card-body py-2">
                        <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                            <div>
                                <strong>Emulator Configs</strong>
                                <div class="small text-muted">No config snapshot has been collected from this Drone yet.</div>
                            </div>
                            <button class="btn btn-outline-primary btn-sm" onclick="loadDeviceConfigs({queue:true})">Collect Configs</button>
                        </div>
                    </div></div>`;
                    return;
                }
                const rows = configs.map((item, index) => {
                    const label = item.relative_path || item.path || item.name || `config-${index + 1}`;
                    const content = item.content || item.text || JSON.stringify(item, null, 2);
                    return {label, root: item.root || '', content};
                });
                const first = rows[0];
                container.innerHTML = `
                    <div class="row">
                        <div class="col-md-3 mb-3">
                                <div class="card log-card">
                                    <div class="card-header">Emulators</div>
                                    <div class="list-group list-group-flush source-selector" id="overmindConfigSources">
                                        ${rows.map((row, index) => `
                                        <button type="button" class="list-group-item list-group-item-action text-start" onclick="selectOvermindConfig(${index})">
                                            <i class="bi bi-file-earmark-code me-2"></i>${escapeHtml(row.label)}
                                        </button>
                                    `).join('')}
                                </div>
                            </div>
                        </div>
                        <div class="col-md-9">
                            <div class="card log-card">
                                <div class="card-header d-flex flex-wrap justify-content-between align-items-center gap-2">
                                    <span id="overmindConfigTitle">Select a config</span>
                                    <div class="d-flex gap-2">
                                        <button class="btn btn-sm btn-outline-primary" onclick="loadDeviceConfigs({queue:true})">Refresh Snapshot</button>
                                    </div>
                                </div>
                                <div class="card-body">
                                    <div id="overmindConfigPath" class="small text-muted mb-2"></div>
                                    <pre id="overmindConfigContent" class="mono bg-dark text-light p-3" style="max-height:600px;overflow:auto;white-space:pre-wrap;">Select a config from the left panel to view its contents.</pre>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
                window.overmindConfigRows = rows;
                if (rows.length) {
                    setTimeout(() => selectOvermindConfig(0), 0);
                }
            }

            function selectOvermindConfig(index) {
                const rows = window.overmindConfigRows || [];
                const row = rows[index];
                if (!row) return;
                document.querySelectorAll('#overmindConfigSources .list-group-item').forEach((node, idx) => node.classList.toggle('active', idx === index));
                const title = document.getElementById('overmindConfigTitle');
                const path = document.getElementById('overmindConfigPath');
                const content = document.getElementById('overmindConfigContent');
                if (title) title.textContent = row.label;
                if (path) path.textContent = row.root || '';
                if (content) content.textContent = row.content || '';
            }

            async function requestDeviceDataSnapshot(kind) {
                if (!selectedDeviceId) return;
                const isLogs = kind === 'logs';
                const container = document.getElementById(isLogs ? 'gamelogs-list' : 'configs-list');
                if (container) {
                    container.innerHTML = `<div class="empty-state">${isLogs ? 'Log' : 'Emulator config'} data is being retrieved and should be available within 30 seconds.</div>`;
                }
                showMessage(`${isLogs ? 'Log' : 'Emulator config'} data is being retrieved and should be available within 30 seconds.`, 'success');
                const actions = isLogs ? ['collect_game_logs', 'collect_log_sources'] : ['collect_emulator_configs'];
                try {
                    for (const actionName of actions) {
                        await queueDeviceAction(actionName, { confirm: false, refreshActions: false, notify: false });
                    }
                    await pollDeviceSnapshot(kind);
                } catch (error) {
                    console.error('Error requesting device data:', error);
                    if (container) container.innerHTML = `<div class="empty-state">Unable to request ${isLogs ? 'log' : 'emulator config'} data.</div>`;
                }
            }

            async function pollDeviceSnapshot(kind) {
                const deadline = Date.now() + 30000;
                const isLogs = kind === 'logs';
                while (Date.now() <= deadline) {
                    await new Promise(resolve => setTimeout(resolve, 5000));
                    const response = await apiGet(`/api/devices/${selectedDeviceId}`);
                    if (!response.ok) throw new Error('Failed to load device details');
                    const device = await response.json();
                    if (isLogs) {
                        const hasLogSources = Array.isArray(device.log_sources?.logs) && device.log_sources.logs.length > 0;
                        const hasGameLogs = Array.isArray(device.game_logs?.sessions) && device.game_logs.sessions.length > 0;
                        if (hasLogSources || hasGameLogs) {
                            await loadGameLogs({ queue: false });
                            return;
                        }
                    } else if (Array.isArray(device.emulator_configs?.configs) && device.emulator_configs.configs.length > 0) {
                        displayDeviceConfigs(device.emulator_configs);
                        return;
                    }
                }
                if (isLogs) {
                    await loadGameLogs({ queue: false });
                } else {
                    await loadDeviceConfigs({ queue: false });
                }
            }

            async function loadDeviceSystems() {
                if (!selectedDeviceId) {
                    document.getElementById('systems-list').innerHTML = '<div class="empty-state">Select a Drone to view systems.</div>';
                    return;
                }
                try {
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/roms`);
                    if (!response.ok) throw new Error('Failed to load device systems');
                    const data = await response.json();
                    currentDeviceSystems = data.systems || {};
                    displaySystemsTree();
                } catch (error) {
                    console.error('Error loading systems:', error);
                }
            }

            let deviceRomSearchDebounce = null;
            function handleDeviceRomSearch(event) {
                const val = (event.target.value || '').trim();
                deviceRomSearchQuery = val;
                masterRomPage = 1;
                // debounce server-side filtering
                if (deviceRomSearchDebounce) clearTimeout(deviceRomSearchDebounce);
                deviceRomSearchDebounce = setTimeout(() => {
                    deviceRomSearchDebounce = null;
                    loadSwarmRomAvailabilityPanel();
                }, 300);
            }

            function setMasterRomPage(page) {
                masterRomPage = Math.max(1, page);
                loadSwarmRomAvailabilityPanel();
            }

            function setSystemPage(systemName, page) {
                systemPageState[systemName] = Math.max(1, page);
                displaySystemsTree();
            }

            function handleDeviceRomFilterChange() {
                // Trigger server-side reload of the master table when filters change
                masterRomPage = 1;
                loadSwarmRomAvailabilityPanel();
            }

            async function populateSystemFilterOptions() {
                // populate systems dropdown from currentDeviceSystems or from server summary
                const select = document.getElementById('device-rom-system-filter');
                if (!select) return;
                select.innerHTML = '<option value="">All systems</option>';
                try {
                    const resp = await apiGet('/api/systems');
                    if (!resp.ok) return;
                    const data = await resp.json();
                    const systems = data.systems || [];
                    systems.forEach(s => {
                        const opt = document.createElement('option');
                        opt.value = s.system_name;
                        opt.text = `${s.system_name} (${s.rom_count})`;
                        select.appendChild(opt);
                    });
                } catch (e) {
                    // ignore
                }
            }

            async function syncSystemFromFilter(systemParam) {
                const system = systemParam || document.getElementById('device-rom-system-filter')?.value || '';
                if (!system) return alert('Select a system to sync');
                if (!selectedDeviceId) return;
                if (!confirm(`Queue sync for system ${system} on this Drone?`)) return;
                try {
                    await syncSystem(system);
                    await loadSyncActivityPanel();
                    await loadSwarmRomAvailabilityPanel();
                } catch (err) {
                    console.error('Error syncing system:', err);
                    showMessage('Failed to queue system sync.', 'error');
                }
            }

            function filteredSystemEntries() {
                const query = deviceRomSearchQuery;
                return Object.entries(currentDeviceSystems).reduce((entries, [systemName, roms]) => {
                    const systemMatches = systemName.toLowerCase().includes(query);
                    const filteredRoms = !query || systemMatches
                        ? roms
                        : roms.filter(rom => String(rom.rom_name || '').toLowerCase().includes(query));
                    if (!query || systemMatches || filteredRoms.length) entries.push([systemName, filteredRoms]);
                    return entries;
                }, []);
            }

            function displaySystemsTree() {
                const container = document.getElementById('systems-list');
                const entries = filteredSystemEntries();
                if (!entries.length) {
                    container.innerHTML = '<div class="empty-state">No systems or ROMs matched your search.</div>';
                    return;
                }
                entries.sort((a, b) => a[0].localeCompare(b[0]));
                container.innerHTML = `
                    <div class="tree-view">
                        ${entries.map(([systemName, roms]) => {
                            const totalBytes = roms.reduce((sum, rom) => sum + Number(rom.file_size || 0), 0);
                            const totalMb = (totalBytes / 1024 / 1024).toFixed(2);
                            const totalPages = Math.max(1, Math.ceil(roms.length / ROMS_PER_PAGE));
                            const currentPage = Math.min(systemPageState[systemName] || 1, totalPages);
                            const start = (currentPage - 1) * ROMS_PER_PAGE;
                            const pageRoms = roms.slice(start, start + ROMS_PER_PAGE);
                            return `
                                <details>
                                    <summary>${systemName} (${roms.length} ROMs, ${totalMb} MB)</summary>
                                    <ul class="list-unstyled ms-3 mt-2">
                                        ${pageRoms.map(rom => {
                                            const path = rom.file_path || rom.relative_path || rom.rom_name || '';
                                            const size = rom.file_size ? ` ${(rom.file_size / 1024 / 1024).toFixed(2)} MB` : '';
                                            return `<li class="py-1 border-bottom small">
                                                <div>${escapeHtml(rom.rom_name || path)}</div>
                                                <div class="text-muted">${escapeHtml(path)}${size ? ` <span>(${size.trim()})</span>` : ''}${rom.rom_md5 ? ` <span class="mono">md5: ${escapeHtml(rom.rom_md5)}</span>` : ''}</div>
                                            </li>`;
                                        }).join('')}
                                    </ul>
                                    <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 ms-3 mt-2 small text-muted">
                                        <span>Showing ${roms.length ? start + 1 : 0}-${Math.min(start + ROMS_PER_PAGE, roms.length)} of ${roms.length}</span>
                                        <div class="btn-group btn-group-sm" role="group" aria-label="${systemName} pages">
                                            <button class="btn btn-outline-secondary" ${currentPage <= 1 ? 'disabled' : ''} onclick="setSystemPage('${systemName.replace(/'/g, "\\'")}', ${currentPage - 1})">Previous</button>
                                            <button class="btn btn-outline-secondary" disabled>Page ${currentPage} of ${totalPages}</button>
                                            <button class="btn btn-outline-secondary" ${currentPage >= totalPages ? 'disabled' : ''} onclick="setSystemPage('${systemName.replace(/'/g, "\\'")}', ${currentPage + 1})">Next</button>
                                        </div>
                                    </div>
                                </details>
                            `;
                        }).join('')}
                    </div>
                `;
                renderDroneAutoSyncPanel();
            }

            function selectedDrone() {
                return currentDevices.find(d => d.device_id === selectedDeviceId) || null;
            }

            function renderDroneNetworkPanel() {
                const container = document.getElementById('drone-network-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const resolved = device.resolved_network || {};
                const ipv4 = resolved.ipv4 || [];
                const ipv6 = resolved.ipv6 || [];
                const cert = device.certificate || {};
                const peerChecks = device.peer_checks || [];
                const info = device.system_info || {};
                const systemRows = [
                    ['Hostname', info.hostname || device.device_name],
                    ['OS', [info.os, info.os_release].filter(Boolean).join(' ')],
                    ['Batocera', info.batocera_version],
                    ['Drone App', info.drone_app_version],
                    ['Architecture', info.architecture],
                    ['CPU', info.cpu ? `${info.cpu.model || 'CPU'} ${info.cpu.count ? `(${info.cpu.count} cores)` : ''}` : ''],
                    ['Memory', info.memory ? `${info.memory.available || 'n/a'} available / ${info.memory.total || 'n/a'} total` : ''],
                    ['Storage', info.disk && info.disk.free_bytes ? `${(Number(info.disk.free_bytes) / 1024 / 1024 / 1024).toFixed(1)} GiB free` : ''],
                    ['Container', info.container === true ? 'yes' : (info.container === false ? 'no' : '')],
                    ['Updated', info.last_system_info_update || info.updated_at],
                ].filter(row => row[1]);
                const latestPeers = Object.values(peerChecks.reduce((acc, check) => {
                    const key = check.target_drone_id || check.target_address || '';
                    if (!key) return acc;
                    if (!acc[key] || String(check.checked_at || '') >= String(acc[key].checked_at || '')) acc[key] = check;
                    return acc;
                }, {}));
                container.innerHTML = `
                    <div class="card"><div class="card-body py-2">
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                            <strong>Swarm Connection</strong>
                            <span class="badge ${device.swarm_connected ? 'text-bg-success' : 'text-bg-secondary'}">${device.swarm_connected ? 'Connected to Swarm' : 'Not Connected to Swarm'}</span>
                        </div>
                        <div class="small text-muted mt-2">IPv4: ${ipv4.length ? ipv4.map(escapeHtml).join(', ') : 'none resolved'}</div>
                        <div class="small text-muted">IPv6: ${ipv6.length ? ipv6.map(escapeHtml).join(', ') : 'none resolved'}</div>
                        <div class="small text-muted">API: ${escapeHtml(device.reachable_url || `${device.scheme || 'https'}://${ipv4[0] || device.device_id}:${device.api_port || 8443}`)}</div>
                        <hr>
                        <strong>Certificate</strong>
                        <div class="small text-muted">Status: ${escapeHtml(cert.status || 'unknown')}</div>
                        <div class="small text-muted">Fingerprint: ${escapeHtml(cert.fingerprint || 'n/a')}</div>
                        <div class="small text-muted">Subject: ${escapeHtml(cert.subject || 'n/a')}</div>
                        <div class="small text-muted">Issuer: ${escapeHtml(cert.issuer || 'n/a')}</div>
                        <div class="small text-muted">SAN: ${(cert.san || []).map(escapeHtml).join(', ') || 'n/a'}</div>
                        <div class="small text-muted">Valid: ${escapeHtml(cert.valid_from || 'n/a')} - ${escapeHtml(cert.valid_until || 'n/a')}</div>
                        <div class="small text-muted">Renewal: ${escapeHtml(cert.renewal_status || 'n/a')}</div>
                        <hr>
                        <strong>System Information</strong>
                        ${systemRows.length ? `<div class="row g-2 mt-1">${systemRows.map(([label, value]) => `
                            <div class="col-12 col-md-6"><div class="small text-muted">${escapeHtml(label)}</div><div class="small">${escapeHtml(String(value || ''))}</div></div>
                        `).join('')}</div>` : '<div class="small text-muted mt-1">No system information reported yet.</div>'}
                        <hr>
                        <strong>Peer-to-Peer Checks</strong>
                        ${latestPeers.length ? latestPeers.map(check => `
                            <div class="mt-2 p-2 rounded border">
                                <div class="d-flex justify-content-between gap-2">
                                    <span class="small">${escapeHtml(check.target_name || check.target_drone_id || 'Peer Drone')}</span>
                                    <span class="badge ${check.status === 'pass' ? 'text-bg-success' : 'text-bg-danger'}">${check.status === 'pass' ? 'RESOLVED' : 'FAILED'}</span>
                                </div>
                                <div class="small text-muted">${escapeHtml(check.target_address || 'n/a')} · ${escapeHtml(check.checked_at || 'n/a')} · ${check.latency_ms ?? 'n/a'} ms</div>
                                ${check.failure_reason ? `<div class="small text-danger">${escapeHtml(check.failure_reason)}</div>` : ''}
                            </div>
                        `).join('') : '<div class="small text-muted mt-1">No peer checks reported yet.</div>'}
                    </div></div>
                `;
            }

            function renderDroneTokenPanel() {
                const container = document.getElementById('drone-token-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                container.innerHTML = `
                    <div class="card"><div class="card-body py-2 d-flex flex-wrap align-items-center justify-content-between gap-2">
                        <div>
                            <strong>Drone Authorization Token</strong>
                            <div class="small text-muted">${device.token_rotated_at ? `Last rotated: ${new Date(device.token_rotated_at).toLocaleString()}` : 'Token hash stored in Overmind'}</div>
                        </div>
                        <button class="btn btn-outline-danger btn-sm" onclick="rotateDroneToken()"><i class="bi bi-arrow-clockwise me-1"></i>Rotate Token</button>
                    </div></div>
                `;
            }

            function renderDroneSpeedPanel() {
                const container = document.getElementById('drone-speed-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const sample = device.last_speed_sample;
                container.innerHTML = `
                    <div class="card"><div class="card-body py-2">
                        <strong>Speed Sample</strong>
                        ${sample ? `<div class="small text-muted mt-1">Down ${sample.download_mbps ?? 'n/a'} Mbps / Up ${sample.upload_mbps ?? 'n/a'} Mbps / Latency ${sample.latency_ms ?? 'n/a'} ms</div>` : '<div class="small text-muted mt-1">No speed sample received yet.</div>'}
                    </div></div>
                `;
            }

            function renderDroneAutoSyncPanel() {
                const container = document.getElementById('drone-auto-sync-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const policy = device.auto_sync_policy || { enabled: false, systems: [] };
                const systems = Object.keys(currentDeviceSystems || {}).sort();
                container.innerHTML = `
                    <div class="card"><div class="card-body py-2">
                        <label class="d-flex gap-2 align-items-center mb-2">
                            <input id="drone-auto-sync-enabled" type="checkbox" ${policy.enabled ? 'checked' : ''}>
                            <strong>Auto-sync ROM metadata from this Drone</strong>
                        </label>
                        <div class="d-flex flex-wrap gap-2 mb-2">
                            ${systems.length ? systems.map(system => `
                                <label class="badge text-bg-secondary">
                                    <input class="drone-auto-sync-system me-1" type="checkbox" value="${escapeHtml(system)}" ${policy.systems.includes(system) ? 'checked' : ''}>
                                    ${escapeHtml(system)}
                                </label>
                            `).join('') : '<span class="small text-muted">Queue ROM & System Metadata to populate system checkboxes.</span>'}
                            ${systems.length ? systems.map(system => `
                                <label class="badge text-bg-secondary">
                                    <input class="drone-auto-sync-system me-1" type="checkbox" value="${escapeHtml(system)}" ${policy.systems.includes(system) ? 'checked' : ''}>
                                    ${escapeHtml(system)}
                                </label>
                            `).join('') : '<span class="small text-muted">Device has not reported system metadata yet.</span>'}
                        </div>
                        <button class="btn btn-primary btn-sm" onclick="saveDroneAutoSyncPolicy()">Save Policy</button>
                    </div></div>
                `;
            }

            function renderDroneMetadataPanel() {
                const container = document.getElementById('device-metadata-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const resolved = device.resolved_network || {};
                const ipv4 = resolved.ipv4 || [];
                const ipv6 = resolved.ipv6 || [];
                const cert = device.certificate || {};
                const info = device.system_info || {};
                const sample = device.last_speed_sample;
                const systemRows = [
                    ['Hostname', info.hostname || device.device_name],
                    ['OS', [info.os, info.os_release].filter(Boolean).join(' ')],
                    ['Batocera', info.batocera_version],
                    ['Drone App', info.drone_app_version],
                    ['Architecture', info.architecture],
                    ['CPU', info.cpu ? `${info.cpu.model || 'CPU'} ${info.cpu.count ? `(${info.cpu.count} cores)` : ''}` : ''],
                    ['Memory', info.memory ? `${info.memory.available || 'n/a'} available / ${info.memory.total || 'n/a'} total` : ''],
                    ['Storage', info.disk && info.disk.free_bytes ? `${(Number(info.disk.free_bytes) / 1024 / 1024 / 1024).toFixed(1)} GiB free` : ''],
                    ['Container', info.container === true ? 'yes' : (info.container === false ? 'no' : '')],
                    ['Updated', info.last_system_info_update || info.updated_at],
                ].filter(row => row[1]);
                container.innerHTML = `
                    <div class="card"><div class="card-body py-2">
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                            <strong>Connection Information</strong>
                            <span class="badge ${device.swarm_connected ? 'text-bg-success' : 'text-bg-secondary'}">${device.swarm_connected ? 'Connected to Swarm' : 'Not Connected to Swarm'}</span>
                        </div>
                        <div class="small text-muted mt-2">IPv4: ${ipv4.length ? ipv4.map(escapeHtml).join(', ') : 'none resolved'}</div>
                        <div class="small text-muted">IPv6: ${ipv6.length ? ipv6.map(escapeHtml).join(', ') : 'none resolved'}</div>
                        <div class="small text-muted">API: ${escapeHtml(device.reachable_url || `${device.scheme || 'https'}://${ipv4[0] || device.device_id}:${device.api_port || 8443}`)}</div>
                        <hr>
                        <strong>Certificate</strong>
                        <div class="small text-muted">Status: ${escapeHtml(cert.status || 'unknown')}</div>
                        <div class="small text-muted">Fingerprint: ${escapeHtml(cert.fingerprint || 'n/a')}</div>
                        <div class="small text-muted">Subject: ${escapeHtml(cert.subject || 'n/a')}</div>
                        <div class="small text-muted">Issuer: ${escapeHtml(cert.issuer || 'n/a')}</div>
                        <div class="small text-muted">SAN: ${(cert.san || []).map(escapeHtml).join(', ') || 'n/a'}</div>
                        <div class="small text-muted">Valid: ${escapeHtml(cert.valid_from || 'n/a')} - ${escapeHtml(cert.valid_until || 'n/a')}</div>
                        <hr>
                        <strong>System Information</strong>
                        ${systemRows.length ? `<div class="row g-2 mt-1">${systemRows.map(([label, value]) => `
                            <div class="col-12 col-md-6"><div class="small text-muted">${escapeHtml(label)}</div><div class="small">${escapeHtml(String(value || ''))}</div></div>
                        `).join('')}</div>` : '<div class="small text-muted mt-1">No system information reported yet.</div>'}
                        <hr>
                        <strong>Speed Sample</strong>
                        ${sample ? `<div class="small text-muted mt-1">Down ${sample.download_mbps ?? 'n/a'} Mbps / Up ${sample.upload_mbps ?? 'n/a'} Mbps / Latency ${sample.latency_ms ?? 'n/a'} ms</div>` : '<div class="small text-muted mt-1">No speed sample received yet.</div>'}
                    </div></div>
                `;
            }

            async function saveDroneAutoSyncPolicy() {
                if (!selectedDeviceId) return;
                const systems = Array.from(document.querySelectorAll('.drone-auto-sync-system:checked')).map(input => input.value);
                const enabled = !!document.getElementById('drone-auto-sync-enabled')?.checked;
                const response = await apiPatch(`/api/devices/${selectedDeviceId}/auto-sync`, { enabled, systems });
                if (!response.ok) throw new Error('Failed to save policy');
                await loadDevices();
                showMessage('Drone sync policy saved.', 'success');
            }

            async function loadSwarmRomAvailabilityPanel() {
                // Render a single master ROM table that shows all known ROMs across the swarm
                // and indicates whether the selected Drone already has each ROM.
                const container = document.getElementById('swarm-rom-availability-panel');
                if (!container || !selectedDeviceId) return;
                try {
                    // prepare server-side filter params
                    const params = new URLSearchParams();
                    const q = (deviceRomSearchQuery || '').trim();
                    const system = document.getElementById('device-rom-system-filter')?.value || '';
                    const status = document.getElementById('device-rom-status-filter')?.value || '';
                    if (q) params.set('q', q);
                    if (system) params.set('system', system);
                    if (status) params.set('status', status);
                    params.set('page', String(masterRomPage));
                    params.set('per_page', String(MASTER_ROM_PAGE_SIZE));
                    const url = `/api/devices/${selectedDeviceId}/master-roms` + (params.toString() ? `?${params.toString()}` : '');
                    const response = await apiGet(url);
                    if (!response.ok) throw new Error('Failed to load swarm ROM availability');
                    const payload = await response.json();
                    const filtered = payload.roms || [];
                    const total = payload.total || filtered.length;
                    const page = payload.page || masterRomPage;
                    const perPage = payload.per_page || MASTER_ROM_PAGE_SIZE;
                    const pageCount = Math.max(1, Math.ceil(total / perPage));
                    masterRomPage = page;

                    const missingCount = filtered.filter(r => !r.present_on_selected).length;
                    const renderPageButton = (pageNumber) => {
                        return `<button class="btn btn-sm ${pageNumber === page ? 'btn-primary' : 'btn-outline-secondary'}" onclick="setMasterRomPage(${pageNumber})">${pageNumber}</button>`;
                    };
                    const paginationButtons = [];
                    if (pageCount <= 7) {
                        for (let i = 1; i <= pageCount; i += 1) paginationButtons.push(renderPageButton(i));
                    } else {
                        const start = Math.max(1, page - 2);
                        const end = Math.min(pageCount, page + 2);
                        if (start > 1) paginationButtons.push(renderPageButton(1));
                        if (start > 2) paginationButtons.push('<span class="px-2">&hellip;</span>');
                        for (let i = start; i <= end; i += 1) paginationButtons.push(renderPageButton(i));
                        if (end < pageCount - 1) paginationButtons.push('<span class="px-2">&hellip;</span>');
                        if (end < pageCount) paginationButtons.push(renderPageButton(pageCount));
                    }

                    container.innerHTML = `
                        <div class="card"><div class="card-body py-2">
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <div class="d-flex gap-2 align-items-center">
                                    <strong>ROMs (Master List)</strong>
                                    <div class="small text-muted">${total} ROMs · ${missingCount} missing here</div>
                                </div>
                                <div class="d-flex gap-2">
                                    <button class="btn btn-outline-secondary btn-sm" onclick="populateSystemFilterOptions()">Refresh systems</button>
                                </div>
                            </div>
                            <div id="sync-system-buttons" class="d-flex flex-wrap gap-2 mb-3"></div>
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <div class="small text-muted">Page ${page} of ${pageCount} · ${perPage} per page</div>
                                <div class="btn-group" role="group" aria-label="Master ROM pagination">
                                    <button class="btn btn-sm btn-outline-secondary" ${page <= 1 ? 'disabled' : ''} onclick="setMasterRomPage(${Math.max(1, page - 1)})">Previous</button>
                                    ${paginationButtons.join('')}
                                    <button class="btn btn-sm btn-outline-secondary" ${page >= pageCount ? 'disabled' : ''} onclick="setMasterRomPage(${Math.min(pageCount, page + 1)})">Next</button>
                                </div>
                            </div>
                            <div class="table-responsive"><table class="table table-sm align-middle"><thead><tr>
                                <th>System</th>
                                <th>ROM</th>
                                <th>Size</th>
                                <th>Source</th>
                                <th>Status</th>
                                <th></th>
                            </tr></thead><tbody>
                                ${filtered.map(row => {
                                    const present = !!row.present_on_selected;
                                    const sources = (row.devices || []).map(d => d.device_name || d.device_id).join(', ');
                                    const preferred = row.preferred_source_name || (row.devices && row.devices[0] && (row.devices[0].device_name || row.devices[0].device_id)) || '';
                                    const sizeText = row.size ? `${(Number(row.size) / 1024 / 1024).toFixed(2)} MB` : (row.file_size ? `${(Number(row.file_size) / 1024 / 1024).toFixed(2)} MB` : '');
                                    const statusLabel = present ? (row.present_label || 'Present') : (row.devices && row.devices.length ? 'Missing' : 'Unavailable');
                                    const showSync = !present && row.devices && row.devices.length;
                                    const rowData = Object.assign({}, row, { preferred_sync_source: row.preferred_source || preferred });
                                    return `
                                        <tr>
                                            <td>${escapeHtml(row.system_name || '')}</td>
                                            <td style="min-width:240px">
                                                <div>${escapeHtml(row.file_path || row.rom_name || '')}</div>
                                                ${row.rom_md5 ? `<div class="small fst-italic text-muted mono">md5: ${escapeHtml(row.rom_md5)}</div>` : ''}
                                            </td>
                                            <td class="text-muted">${escapeHtml(sizeText)}</td>
                                            <td class="text-muted">${escapeHtml(sources || preferred)}</td>
                                            <td><span class="badge ${present ? 'text-bg-success' : (row.devices && row.devices.length ? 'text-bg-secondary' : 'text-bg-danger')}">${escapeHtml(statusLabel)}</span></td>
                                            <td>
                                                ${showSync ? `<button class="btn btn-primary btn-sm" onclick='syncRom(${JSON.stringify(rowData).replace(/'/g, "'")})'>Sync</button>` : ''}
                                            </td>
                                        </tr>
                                    `;
                                }).join('')}
                            </tbody></table></div>
                            ${total ? '' : '<div class="small text-muted">No ROMs found for this filter.</div>'}
                        </div></div>
                    `;
                    // populate per-system Sync buttons for missing systems
                    try {
                        const btnContainer = document.getElementById('sync-system-buttons');
                        if (btnContainer) {
                            btnContainer.innerHTML = '';
                            const missingBySystem = filtered.reduce((acc, r) => {
                                if (!r.present_on_selected) {
                                    const s = r.system_name || 'Unknown';
                                    acc[s] = (acc[s] || 0) + 1;
                                }
                                return acc;
                            }, {});
                            Object.keys(missingBySystem).sort().forEach(s => {
                                const btn = document.createElement('button');
                                btn.className = 'btn btn-outline-primary btn-sm';
                                btn.textContent = `Sync ${s} (${missingBySystem[s]})`;
                                btn.onclick = () => syncSystemFromFilter(s);
                                btnContainer.appendChild(btn);
                            });
                        }
                    } catch (e) {
                        // ignore
                    }
                    // ensure system filter has options
                    populateSystemFilterOptions();
                } catch (error) {
                    console.error('Error loading master ROM table:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load ROMs.</div>';
                }
            }

            async function syncRom(row) {
                try {
                    const response = await fetch(`/api/devices/${selectedDeviceId}/sync-rom`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}`},
                        body: JSON.stringify(row)
                    });
                    if (!response.ok) throw new Error('Failed to queue ROM sync');
                    showMessage('ROM sync queued. The Drone will choose the source peer automatically.', 'success');
                    await loadSyncActivityPanel();
                    // Refresh the master ROM table so the Sync button disappears once the Drone reports the ROM
                    await loadSwarmRomAvailabilityPanel();
                } catch (error) {
                    console.error('Error queuing ROM sync:', error);
                    showMessage('Failed to queue ROM sync.', 'error');
                }
            }

            async function syncSystem(systemName) {
                const response = await fetch(`/api/devices/${selectedDeviceId}/sync-system`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}`},
                    body: JSON.stringify({ system_name: systemName })
                });
                if (!response.ok) throw new Error('Failed to queue system sync');
                showMessage('System sync queued. The Drone will choose source peers automatically.', 'success');
                await loadSyncActivityPanel();
            }

            function setMasterBiosPage(page) {
                masterBiosPage = Math.max(1, page);
                loadDeviceBiosPanel();
            }

            function handleBiosSearch(event) {
                biosSearchQuery = event.target.value || '';
                masterBiosPage = 1;
                loadDeviceBiosPanel();
            }

            function handleBiosStatusFilter(event) {
                biosStatusFilter = event.target.value || '';
                masterBiosPage = 1;
                loadDeviceBiosPanel();
            }

            async function loadDeviceBiosPanel() {
                const container = document.getElementById('device-bios-panel');
                if (!container || !selectedDeviceId) return;
                try {
                    const params = new URLSearchParams();
                    if ((biosSearchQuery || '').trim()) params.set('q', biosSearchQuery.trim());
                    if (biosStatusFilter) params.set('status', biosStatusFilter);
                    params.set('page', String(masterBiosPage));
                    params.set('per_page', String(MASTER_ROM_PAGE_SIZE));
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/master-bios?${params.toString()}`);
                    if (!response.ok) throw new Error('Failed to load BIOS inventory');
                    const payload = await response.json();
                    const rows = payload.bios || [];
                    const total = payload.total || rows.length;
                    const page = payload.page || masterBiosPage;
                    const perPage = payload.per_page || MASTER_ROM_PAGE_SIZE;
                    const pageCount = Math.max(1, Math.ceil(total / perPage));
                    masterBiosPage = page;
                    const missingCount = rows.filter(row => !row.present_on_selected).length;
                    const pagination = `
                        <div class="btn-group" role="group" aria-label="BIOS pagination">
                            <button class="btn btn-sm btn-outline-secondary" ${page <= 1 ? 'disabled' : ''} onclick="setMasterBiosPage(${Math.max(1, page - 1)})">Previous</button>
                            <button class="btn btn-sm btn-outline-secondary" disabled>Page ${page} of ${pageCount}</button>
                            <button class="btn btn-sm btn-outline-secondary" ${page >= pageCount ? 'disabled' : ''} onclick="setMasterBiosPage(${Math.min(pageCount, page + 1)})">Next</button>
                        </div>
                    `;
                    container.innerHTML = `
                        <div class="mb-3 rom-browser-toolbar d-flex flex-wrap align-items-center gap-2">
                            <div style="flex:1;min-width:220px">
                                <label class="form-label" for="device-bios-search">Search BIOS files</label>
                                <input id="device-bios-search" class="form-control" type="search" placeholder="Type to filter BIOS files" value="${escapeHtml(biosSearchQuery)}" oninput="handleBiosSearch(event)">
                            </div>
                            <div style="min-width:160px">
                                <label class="form-label" for="device-bios-status-filter">Status</label>
                                <select id="device-bios-status-filter" class="form-select" onchange="handleBiosStatusFilter(event)">
                                    <option value="" ${!biosStatusFilter ? 'selected' : ''}>All</option>
                                    <option value="missing" ${biosStatusFilter === 'missing' ? 'selected' : ''}>Missing</option>
                                    <option value="present" ${biosStatusFilter === 'present' ? 'selected' : ''}>Present</option>
                                </select>
                            </div>
                        </div>
                        <div class="card"><div class="card-body py-2">
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <div class="d-flex gap-2 align-items-center">
                                    <strong>BIOS (Master List)</strong>
                                    <div class="small text-muted">${total} files · ${missingCount} missing here</div>
                                </div>
                                ${pagination}
                            </div>
                            <div class="table-responsive"><table class="table table-sm align-middle"><thead><tr>
                                <th>BIOS</th>
                                <th>Size</th>
                                <th>Source</th>
                                <th>Status</th>
                                <th></th>
                            </tr></thead><tbody>
                                ${rows.map(row => {
                                    const present = !!row.present_on_selected;
                                    const sources = (row.devices || []).map(d => d.device_name || d.device_id).join(', ');
                                    const sizeText = row.file_size ? `${(Number(row.file_size) / 1024 / 1024).toFixed(2)} MB` : '';
                                    const showSync = !present && row.devices && row.devices.length;
                                    const rowData = Object.assign({}, row);
                                    return `
                                        <tr>
                                            <td style="min-width:260px">
                                                <div>${escapeHtml(row.file_path || row.bios_name || '')}</div>
                                                ${row.bios_md5 ? `<div class="small fst-italic text-muted mono">md5: ${escapeHtml(row.bios_md5)}</div>` : ''}
                                            </td>
                                            <td class="text-muted">${escapeHtml(sizeText)}</td>
                                            <td class="text-muted">${escapeHtml(sources)}</td>
                                            <td><span class="badge ${present ? 'text-bg-success' : (row.devices && row.devices.length ? 'text-bg-secondary' : 'text-bg-danger')}">${present ? 'Present' : (row.devices && row.devices.length ? 'Missing' : 'Unavailable')}</span></td>
                                            <td>${showSync ? `<button class="btn btn-primary btn-sm" onclick='syncBios(${JSON.stringify(rowData).replace(/'/g, "'")})'>Sync</button>` : ''}</td>
                                        </tr>
                                    `;
                                }).join('')}
                            </tbody></table></div>
                            ${total ? '' : '<div class="small text-muted">No BIOS files found for this filter.</div>'}
                        </div></div>
                    `;
                } catch (error) {
                    console.error('Error loading BIOS inventory:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load BIOS files.</div>';
                }
            }

            async function syncBios(row) {
                try {
                    const response = await fetch(`/api/devices/${selectedDeviceId}/sync-bios`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}`},
                        body: JSON.stringify(row)
                    });
                    if (!response.ok) throw new Error('Failed to queue BIOS sync');
                    showMessage('BIOS sync queued. The Drone will choose the source peer automatically.', 'success');
                    await loadSyncActivityPanel();
                    await loadDeviceBiosPanel();
                } catch (error) {
                    console.error('Error queuing BIOS sync:', error);
                    showMessage('Failed to queue BIOS sync.', 'error');
                }
            }

            function setMasterArtworkPage(page) {
                masterArtworkPage = Math.max(1, page);
                loadDeviceArtworkPanel();
            }

            function handleArtworkSearch(event) {
                artworkSearchQuery = event.target.value || '';
                masterArtworkPage = 1;
                loadDeviceArtworkPanel();
            }

            function handleArtworkStatusFilter(event) {
                artworkStatusFilter = event.target.value || '';
                masterArtworkPage = 1;
                loadDeviceArtworkPanel();
            }

            function handleArtworkTypeFilter(event) {
                artworkTypeFilter = event.target.value || '';
                masterArtworkPage = 1;
                loadDeviceArtworkPanel();
            }

            function normalizeArtworkSelection(values, allValue) {
                const unique = [];
                (values || []).forEach(value => {
                    const text = String(value || '').trim();
                    if (text && text !== allValue && !unique.includes(text)) unique.push(text);
                });
                return unique;
            }

            function handleArtworkSourceFilter(event) {
                const input = event.target;
                if (!input) return;
                if (input.value === 'any') {
                    artworkSourceDeviceFilter = [];
                } else {
                    const checked = Array.from(document.querySelectorAll('.artwork-source-filter:checked')).map(el => el.value);
                    artworkSourceDeviceFilter = normalizeArtworkSelection(checked, 'any');
                }
                loadDeviceArtworkPanel();
            }

            function handleArtworkSystemFilter(event) {
                const input = event.target;
                if (!input) return;
                if (input.value === 'all') {
                    artworkSystemFilter = [];
                } else {
                    const checked = Array.from(document.querySelectorAll('.artwork-system-filter:checked')).map(el => el.value);
                    artworkSystemFilter = normalizeArtworkSelection(checked, 'all');
                }
                loadDeviceArtworkPanel();
            }

            function toggleArtworkDropdown(event, id) {
                event.preventDefault();
                event.stopPropagation();
                const menu = document.getElementById(`${id}-menu`);
                const button = document.getElementById(`${id}-button`);
                if (!menu || !button) return;
                const willOpen = !menu.classList.contains('show');
                document.querySelectorAll('.artwork-filter-menu.show').forEach(openMenu => {
                    if (openMenu !== menu) {
                        openMenu.classList.remove('show');
                        const openButtonId = openMenu.id.replace(/-menu$/, '-button');
                        document.getElementById(openButtonId)?.setAttribute('aria-expanded', 'false');
                    }
                });
                menu.classList.toggle('show', willOpen);
                button.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            }

            function closeArtworkDropdowns() {
                document.querySelectorAll('.artwork-filter-menu.show').forEach(menu => {
                    menu.classList.remove('show');
                    const buttonId = menu.id.replace(/-menu$/, '-button');
                    document.getElementById(buttonId)?.setAttribute('aria-expanded', 'false');
                });
            }
            document.addEventListener('click', closeArtworkDropdowns);

            function renderArtworkCheckboxDropdown({id, label, allLabel, allValue, items, selectedValues, inputClass, onChange}) {
                const selected = normalizeArtworkSelection(selectedValues, allValue);
                const activeLabel = selected.length ? `${selected.length} selected` : allLabel;
                const options = (items || []).map(item => {
                    const value = typeof item === 'string' ? item : item.value;
                    const text = typeof item === 'string' ? item : item.label;
                    return `
                        <label class="dropdown-item app-dropdown-check form-check mb-0">
                            <input class="form-check-input ${inputClass}" type="checkbox" value="${escapeHtml(value)}" ${selected.includes(value) ? 'checked' : ''} onchange="${onChange}(event)">
                            <span class="form-check-label ms-1">${escapeHtml(text)}</span>
                        </label>
                    `;
                }).join('');
                return `
                    <div style="min-width:190px">
                        <label class="form-label" for="${id}-button">${escapeHtml(label)}</label>
                        <div class="dropdown app-checkbox-dropdown">
                            <button id="${id}-button" class="btn btn-outline-primary dropdown-toggle w-100 text-start" type="button" onclick="toggleArtworkDropdown(event, '${id}')" aria-expanded="false">${escapeHtml(activeLabel)}</button>
                            <div id="${id}-menu" class="dropdown-menu filter-dropdown-menu artwork-filter-menu app-checkbox-menu" onclick="event.stopPropagation()">
                                <label class="dropdown-item app-dropdown-check form-check mb-0">
                                    <input class="form-check-input ${inputClass}" type="checkbox" value="${escapeHtml(allValue)}" ${selected.length ? '' : 'checked'} onchange="${onChange}(event)">
                                    <span class="form-check-label ms-1">${escapeHtml(allLabel)}</span>
                                </label>
                                <div class="dropdown-divider"></div>
                                ${options || '<div class="dropdown-item small text-muted">No options available.</div>'}
                            </div>
                        </div>
                    </div>
                `;
            }

            function selectedArtworkSourceDevicesForRow(row) {
                const sources = Array.isArray(row?.devices) ? row.devices : [];
                if (!artworkSourceDeviceFilter.length) return sources;
                return sources.filter(source => artworkSourceDeviceFilter.includes(source.device_id));
            }

            async function loadDeviceArtworkPanel() {
                const container = document.getElementById('device-artwork-panel');
                if (!container || !selectedDeviceId) return;
                try {
                    if (!currentDevices.length) await loadDevices();
                    const params = new URLSearchParams();
                    if ((artworkSearchQuery || '').trim()) params.set('q', artworkSearchQuery.trim());
                    if (artworkStatusFilter) params.set('status', artworkStatusFilter);
                    if (artworkTypeFilter) params.set('artwork_type', artworkTypeFilter);
                    params.set('page', String(masterArtworkPage));
                    params.set('per_page', String(MASTER_ROM_PAGE_SIZE));
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/master-artwork?${params.toString()}`);
                    if (!response.ok) throw new Error('Failed to load artwork inventory');
                    const payload = await response.json();
                    const rows = payload.artwork || [];
                    const total = payload.total || rows.length;
                    const page = payload.page || masterArtworkPage;
                    const perPage = payload.per_page || MASTER_ROM_PAGE_SIZE;
                    const pageCount = Math.max(1, Math.ceil(total / perPage));
                    masterArtworkPage = page;
                    const missingCount = rows.filter(row => !row.present_on_selected).length;
                    let systemOptions = Array.from(new Set(rows.map(row => row.system_name || row.system).filter(Boolean))).sort((a, b) => a.localeCompare(b));
                    try {
                        const systemsResponse = await apiGet('/api/systems');
                        if (systemsResponse.ok) {
                            const systemsPayload = await systemsResponse.json();
                            systemOptions = Array.from(new Set((systemsPayload.systems || []).map(row => row.system_name).filter(Boolean))).sort((a, b) => a.localeCompare(b));
                        }
                    } catch (error) {
                        // Keep page-derived system options when the global systems list is unavailable.
                    }
                    const sourceOptions = currentDevices
                        .filter(device => device && device.device_id && device.device_id !== selectedDeviceId)
                        .sort((a, b) => String(a.device_name || a.device_id).localeCompare(String(b.device_name || b.device_id)))
                        .map(device => ({value: device.device_id, label: device.device_name || device.device_id}));
                    const sourceDropdown = renderArtworkCheckboxDropdown({
                        id: 'device-artwork-source-filter',
                        label: 'Sync from',
                        allLabel: 'Any source',
                        allValue: 'any',
                        items: sourceOptions,
                        selectedValues: artworkSourceDeviceFilter,
                        inputClass: 'artwork-source-filter',
                        onChange: 'handleArtworkSourceFilter',
                    });
                    const systemDropdown = renderArtworkCheckboxDropdown({
                        id: 'device-artwork-system-filter',
                        label: 'Systems to sync',
                        allLabel: 'All systems',
                        allValue: 'all',
                        items: systemOptions,
                        selectedValues: artworkSystemFilter,
                        inputClass: 'artwork-system-filter',
                        onChange: 'handleArtworkSystemFilter',
                    });
                    const typeOptions = ['', 'image', 'thumbnail', 'marquee', 'fanart', 'boxart', 'video', 'wheel', 'manual'].map(value => {
                        const label = value || 'All types';
                        return `<option value="${escapeHtml(value)}" ${artworkTypeFilter === value ? 'selected' : ''}>${escapeHtml(label)}</option>`;
                    }).join('');
                    const pagination = `
                        <div class="btn-group" role="group" aria-label="Artwork pagination">
                            <button class="btn btn-sm btn-outline-secondary" ${page <= 1 ? 'disabled' : ''} onclick="setMasterArtworkPage(${Math.max(1, page - 1)})">Previous</button>
                            <button class="btn btn-sm btn-outline-secondary" disabled>Page ${page} of ${pageCount}</button>
                            <button class="btn btn-sm btn-outline-secondary" ${page >= pageCount ? 'disabled' : ''} onclick="setMasterArtworkPage(${Math.min(pageCount, page + 1)})">Next</button>
                        </div>
                    `;
                    container.innerHTML = `
                        <div class="mb-3 rom-browser-toolbar d-flex flex-wrap align-items-center gap-2">
                            <div style="flex:1;min-width:220px">
                                <label class="form-label" for="device-artwork-search">Search artwork</label>
                                <input id="device-artwork-search" class="form-control" type="search" placeholder="Type to filter ROM artwork" value="${escapeHtml(artworkSearchQuery)}" oninput="handleArtworkSearch(event)">
                            </div>
                            <div style="min-width:150px">
                                <label class="form-label" for="device-artwork-type-filter">Type</label>
                                <select id="device-artwork-type-filter" class="form-select" onchange="handleArtworkTypeFilter(event)">${typeOptions}</select>
                            </div>
                            <div style="min-width:160px">
                                <label class="form-label" for="device-artwork-status-filter">Status</label>
                                <select id="device-artwork-status-filter" class="form-select" onchange="handleArtworkStatusFilter(event)">
                                    <option value="" ${!artworkStatusFilter ? 'selected' : ''}>All</option>
                                    <option value="missing" ${artworkStatusFilter === 'missing' ? 'selected' : ''}>Missing</option>
                                    <option value="present" ${artworkStatusFilter === 'present' ? 'selected' : ''}>Present</option>
                                </select>
                            </div>
                            ${sourceDropdown}
                            ${systemDropdown}
                            <div style="min-width:170px">
                                <label class="form-label d-block">&nbsp;</label>
                                <button class="btn btn-primary w-100" onclick="syncAllArtwork()">Sync All ROMs</button>
                            </div>
                        </div>
                        <div class="card"><div class="card-body py-2">
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <div class="d-flex gap-2 align-items-center">
                                    <strong>Artwork (Master List)</strong>
                                    <div class="small text-muted">${total} assets · ${missingCount} missing here</div>
                                </div>
                                ${pagination}
                            </div>
                            <div class="table-responsive"><table class="table table-sm align-middle"><thead><tr>
                                <th>ROM</th>
                                <th>Type</th>
                                <th>Source</th>
                                <th>Status</th>
                                <th></th>
                            </tr></thead><tbody>
                                ${rows.map(row => {
                                    const present = !!row.present_on_selected;
                                    const sources = (row.devices || []).map(d => d.device_name || d.device_id).join(', ');
                                    const selectedSources = selectedArtworkSourceDevicesForRow(row);
                                    const showSync = !present && selectedSources.length;
                                    const rowData = Object.assign({}, row, { devices: selectedSources });
                                    return `
                                        <tr>
                                            <td style="min-width:280px">
                                                <div>${escapeHtml(row.system_name || row.system || '')} / ${escapeHtml(row.rom_path || row.file_path || row.rom_name || '')}</div>
                                                ${row.title ? `<div class="small text-muted">${escapeHtml(row.title)}</div>` : ''}
                                            </td>
                                            <td><span class="badge text-bg-light">${escapeHtml(row.artwork_type || '')}</span></td>
                                            <td class="text-muted">${escapeHtml(sources)}</td>
                                            <td><span class="badge ${present ? 'text-bg-success' : (row.devices && row.devices.length ? 'text-bg-secondary' : 'text-bg-danger')}">${present ? 'Present' : (row.devices && row.devices.length ? 'Missing' : 'Unavailable')}</span></td>
                                            <td>${showSync ? `<button class="btn btn-primary btn-sm" onclick='syncArtwork(${JSON.stringify(rowData).replace(/'/g, "'")})'>Sync</button>` : ''}</td>
                                        </tr>
                                    `;
                                }).join('')}
                            </tbody></table></div>
                            ${total ? '' : '<div class="small text-muted">No artwork found for this filter.</div>'}
                        </div></div>
                    `;
                } catch (error) {
                    console.error('Error loading artwork inventory:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load artwork.</div>';
                }
            }

            async function syncAllArtwork() {
                if (!selectedDeviceId) return;
                try {
                    const payload = {
                        systems: artworkSystemFilter,
                        devices: artworkSourceDeviceFilter,
                    };
                    if (artworkTypeFilter) payload.artwork_type = artworkTypeFilter;
                    const response = await fetch(`/api/devices/${selectedDeviceId}/sync-artwork-bulk`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}`},
                        body: JSON.stringify(payload)
                    });
                    if (!response.ok) throw new Error('Failed to queue artwork sync');
                    const result = await response.json();
                    const count = Number(result.queued_artwork_count || result.action_count || 0);
                    showMessage(count ? `Queued ${count} artwork sync${count === 1 ? '' : 's'}.` : 'No missing artwork matched those selections.', 'success');
                    await loadSyncActivityPanel();
                    await loadDeviceArtworkPanel();
                } catch (error) {
                    console.error('Error queuing bulk artwork sync:', error);
                    showMessage('Failed to queue bulk artwork sync.', 'error');
                }
            }

            async function syncArtwork(row) {
                try {
                    const selectedSources = selectedArtworkSourceDevicesForRow(row);
                    const payload = Object.assign({}, row, { devices: selectedSources });
                    const response = await fetch(`/api/devices/${selectedDeviceId}/sync-artwork`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}`},
                        body: JSON.stringify(payload)
                    });
                    if (!response.ok) throw new Error('Failed to queue artwork sync');
                    showMessage('Artwork sync queued. The Drone will choose the source peer automatically.', 'success');
                    await loadSyncActivityPanel();
                    await loadDeviceArtworkPanel();
                } catch (error) {
                    console.error('Error queuing artwork sync:', error);
                    showMessage('Failed to queue artwork sync.', 'error');
                }
            }

            async function loadSyncActivityPanel() {
                const container = document.getElementById('drone-sync-activity-panel');
                if (!container || !selectedDeviceId) return;
                try {
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/sync-activity`);
                    if (!response.ok) throw new Error('Failed to load sync activity');
                    const rows = ((await response.json()).activity || []).slice(0, 20);
                    container.innerHTML = `<div class="card"><div class="card-body py-2"><strong>Sync Activity</strong>
                        ${rows.length ? rows.map(row => {
                            const duration = formatDuration(row);
                            const durationText = duration ? (row.status === 'failed' ? `Failed after ${duration}` : row.status === 'completed' ? `Completed in ${duration}` : duration) : '';
                            const refreshText = row.inventory_refresh_status ? `Inventory ${row.inventory_refresh_status}${row.inventory_refresh_duration_ms !== undefined && row.inventory_refresh_duration_ms !== null ? ` in ${row.inventory_refresh_duration_ms}ms` : ''}` : '';
                            const assetParts = [row.asset_type === 'bios' ? 'bios' : (row.system || ''), row.bios_name || row.rom_path || row.rom_name || row.relative_path || '', row.artwork_type || ''].filter(Boolean);
                            return `<div class="mt-2 small">
                            <span class="badge ${row.status === 'completed' ? 'text-bg-success' : row.status === 'failed' ? 'text-bg-danger' : 'text-bg-secondary'}">${escapeHtml(row.status || 'pending')}</span>
                            ${escapeHtml(assetParts.join(' / '))}
                            ${row.source_drone_id ? `from ${escapeHtml(row.source_drone_id)}` : ''}
                            ${durationText ? `<span class="text-muted ms-1">${escapeHtml(durationText)}</span>` : ''}
                            ${refreshText ? `<div class="text-muted">${escapeHtml(refreshText)}</div>` : ''}
                            ${row.failure_reason ? `<div class="text-danger">${escapeHtml(row.failure_reason)}</div>` : ''}
                        </div>`;
                        }).join('') : '<div class="small text-muted mt-1">No sync activity yet.</div>'}
                    </div></div>`;
                } catch (error) {
                    container.innerHTML = '<div class="empty-state">Unable to load sync activity.</div>';
                }
            }

            async function rotateDroneToken() {
                if (!selectedDeviceId || !window.confirm('Rotate this Drone token? The old token will stop working immediately.')) return;
                const response = await fetch(`/api/devices/${selectedDeviceId}/token/rotate`, {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (!response.ok) throw new Error('Failed to rotate token');
                const data = await response.json();
                await loadDevices();
                showTokenModal(data.drone_token, 'New Drone Authorization Token');
            }

            function updateSelectedDeviceWorkspace() {
                const workspace = document.getElementById('selected-device-workspace');
                const listView = document.getElementById('device-list-view');
                const title = document.getElementById('selected-device-title');
                const idNode = document.getElementById('selected-device-id');
                if (!workspace) return;
                if (!selectedDeviceId) {
                    workspace.style.display = 'none';
                    if (listView) listView.style.display = 'block';
                    return;
                }
                const device = currentDevices.find(d => d.device_id === selectedDeviceId);
                if (listView) listView.style.display = 'none';
                workspace.style.display = 'block';
                if (title) title.textContent = device ? device.device_name : 'Selected Drone';
                if (idNode) idNode.textContent = device ? `Drone ID: ${device.device_id}` : `Drone ID: ${selectedDeviceId}`;
                renderDroneNetworkPanel();
                renderDroneTokenPanel();
                renderDroneSpeedPanel();
                loadSwarmRomAvailabilityPanel();
                loadSyncActivityPanel();
            }

            function backToDevices() {
                selectedDeviceId = null;
                currentDeviceView = 'systems';
                setRoute('devices', null, 'systems');
            }

            function switchDeviceView(viewName, buttonEl = null, updateUrl = true) {
                if (!selectedDeviceId) return;
                currentDeviceView = ['bios', 'artwork', 'gamelogs', 'configs', 'actions', 'metadata'].includes(viewName) ? viewName : 'systems';
                document.querySelectorAll('.device-view-btn').forEach(btn => btn.classList.remove('active'));
                const activeBtn = buttonEl || document.querySelector(`.device-view-btn[data-device-view="${currentDeviceView}"]`);
                if (activeBtn) activeBtn.classList.add('active');

                const systemsPanel = document.getElementById('device-systems-panel');
                const syncActivityPanel = document.getElementById('drone-sync-activity-panel');
                const biosPanel = document.getElementById('device-bios-panel');
                const artworkPanel = document.getElementById('device-artwork-panel');
                const gamelogsPanel = document.getElementById('device-gamelogs-panel');
                const configsPanel = document.getElementById('device-configs-panel');
                const actionsPanel = document.getElementById('device-actions-panel');
                const metadataPanel = document.getElementById('device-metadata-panel');
                if (systemsPanel) systemsPanel.style.display = currentDeviceView === 'systems' ? 'block' : 'none';
                if (syncActivityPanel) syncActivityPanel.style.display = (currentDeviceView === 'systems' || currentDeviceView === 'bios' || currentDeviceView === 'artwork') ? 'block' : 'none';
                if (biosPanel) biosPanel.style.display = currentDeviceView === 'bios' ? 'block' : 'none';
                if (artworkPanel) artworkPanel.style.display = currentDeviceView === 'artwork' ? 'block' : 'none';
                if (gamelogsPanel) gamelogsPanel.style.display = currentDeviceView === 'gamelogs' ? 'block' : 'none';
                if (configsPanel) configsPanel.style.display = currentDeviceView === 'configs' ? 'block' : 'none';
                if (actionsPanel) actionsPanel.style.display = currentDeviceView === 'actions' ? 'block' : 'none';
                if (metadataPanel) metadataPanel.style.display = currentDeviceView === 'metadata' ? 'block' : 'none';

                if (currentDeviceView === 'systems') loadSwarmRomAvailabilityPanel();
                if (currentDeviceView === 'systems' || currentDeviceView === 'bios' || currentDeviceView === 'artwork') loadSyncActivityPanel();
                if (currentDeviceView === 'bios') loadDeviceBiosPanel();
                if (currentDeviceView === 'artwork') loadDeviceArtworkPanel();
                if (currentDeviceView === 'gamelogs') loadGameLogs({queue: true});
                if (currentDeviceView === 'configs') loadDeviceConfigs({queue: true});
                if (currentDeviceView === 'metadata') {
                    renderDroneMetadataPanel();
                }
                if (actionRefreshTimer) clearInterval(actionRefreshTimer);
                actionRefreshTimer = null;
                if (currentDeviceView === 'actions') {
                    loadDeviceActions();
                    actionRefreshTimer = setInterval(loadDeviceActions, 5000);
                }
                if (updateUrl) setRoute('devices', selectedDeviceId, currentDeviceView);
            }

            function setRoute(tabName, deviceId = selectedDeviceId, deviceView = currentDeviceView, swarmView = null) {
                let hash = `#/${tabName}`;
                if (tabName === 'devices') {
                    const swarmPath = isSharedSwarmSelected() ? `/swarm/${encodeURIComponent(selectedSwarmId)}` : '';
                    if (!deviceId && swarmView) hash = `#/devices${swarmPath}/swarm/${swarmView}`;
                    if (deviceId) hash = `#/devices${swarmPath}/device/${encodeURIComponent(deviceId)}/${deviceView || 'systems'}`;
                }
                if (window.location.hash !== hash) window.location.hash = hash; else applyRouteFromHash();
            }

            function parseRoute() {
                const raw = window.location.hash || '#/devices';
                const clean = raw.replace(/^#\/?/, '');
                const parts = clean.split('/').filter(Boolean);
                const allowed = ['devices', 'hive', 'profile', 'help'];
                if ((parts[0] === 'systems' || parts[0] === 'bios' || parts[0] === 'gamelogs' || parts[0] === 'configs' || parts[0] === 'actions' || parts[0] === 'metadata') && parts[1]) {
                    return { tab: 'devices', deviceId: decodeURIComponent(parts[1]), deviceView: parts[0] };
                }
                const tab = allowed.includes(parts[0]) ? parts[0] : 'devices';
                if (tab === 'devices' && parts[1] === 'swarm') {
                    if (parts[3] === 'swarm') {
                        const swarmViews = ['drones', 'downloads', 'sync-activity', 'master-list'];
                        return { tab, swarmId: decodeURIComponent(parts[2]), deviceId: null, deviceView: 'systems', swarmView: swarmViews.includes(parts[4]) ? parts[4] : 'drones' };
                    }
                    if (parts[3] === 'device') {
                        return { tab, swarmId: decodeURIComponent(parts[2]), deviceId: parts[4] ? decodeURIComponent(parts[4]) : null, deviceView: ['bios', 'artwork', 'gamelogs', 'configs', 'actions', 'metadata'].includes(parts[5]) ? parts[5] : 'systems', swarmView: 'drones' };
                    }
                    const swarmViews = ['drones', 'downloads', 'sync-activity', 'master-list'];
                    return { tab, deviceId: null, deviceView: 'systems', swarmView: swarmViews.includes(parts[2]) ? parts[2] : 'drones' };
                }
                if (tab === 'devices' && parts[1] === 'device') {
                    return { tab, deviceId: parts[2] ? decodeURIComponent(parts[2]) : null, deviceView: ['bios', 'artwork', 'gamelogs', 'configs', 'actions', 'metadata'].includes(parts[3]) ? parts[3] : 'systems', swarmView: 'drones' };
                }
                const deviceId = tab === 'devices' && parts[1] ? decodeURIComponent(parts[1]) : null;
                const deviceView = tab === 'devices' && ['bios', 'artwork', 'gamelogs', 'configs', 'actions', 'metadata'].includes(parts[2]) ? parts[2] : 'systems';
                return { tab, deviceId, deviceView, swarmView: 'drones' };
            }

            function applyRouteFromHash() {
                const route = parseRoute();
                routeSwarmId = route.swarmId || null;
                if (route.tab === 'devices' && route.swarmId && currentSwarms.some(s => s.id === route.swarmId)) {
                    selectedSwarmId = route.swarmId;
                    localStorage.setItem('selected_swarm_id', selectedSwarmId);
                }
                if (route.tab === 'devices' && !route.deviceId) {
                    selectedDeviceId = null;
                } else if (route.deviceId && currentDevices.some(d => d.device_id === route.deviceId)) {
                    selectedDeviceId = route.deviceId;
                    currentDeviceView = route.deviceView || 'systems';
                }
                updateSelectedDeviceSummary();
                updateSelectedDeviceWorkspace();
                switchTab(route.tab, null, false);
                if (selectedDeviceId && route.tab === 'devices') switchDeviceView(currentDeviceView, null, false);
                if (!selectedDeviceId && route.tab === 'devices') {
                    const view = route.swarmView || 'drones';
                    if (view === 'downloads') showSwarmDownloads(false);
                    else if (view === 'sync-activity') showSwarmSyncActivity(false);
                    else if (view === 'master-list') showSwarmMasterList(false);
                    else showSwarmHome(false);
                }
                updateSharedSwarmNavButton();
            }

            async function loadDeviceActions() {
                const container = document.getElementById('actions-list');
                if (!selectedDeviceId || !container) return;
                try {
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/actions`);
                    if (!response.ok) throw new Error('Failed to load device actions');
                    const data = await response.json();
                    const actions = data.actions || [];
                    if (!actions.length) {
                        container.innerHTML = '<div class="empty-state">No actions queued yet.</div>';
                        return;
                    }
                    container.innerHTML = actions.map(action => {
                        const result = action.result || null;
                        const resultSummary = summarizeActionResult(result);
                        return `
                        <div class="card mb-2 shadow-sm">
                            <div class="card-body py-2">
                                <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                                    <strong>${formatActionName(action.action)}</strong>
                                    <span class="badge text-bg-secondary">${action.status}</span>
                                </div>
                                <div class="small text-muted mt-1">Created: ${action.created_at ? new Date(action.created_at).toLocaleString() : 'n/a'}</div>
                                ${action.completed_at ? `<div class="small text-muted mt-1">Completed: ${new Date(action.completed_at).toLocaleString()}</div>` : ''}
                                ${action.message ? `<div class="small mt-1">${action.message}</div>` : ''}
                                ${result ? `
                                    <div class="small text-muted mt-2">${resultSummary}</div>
                                    <details class="mt-2">
                                        <summary class="small">View returned data</summary>
                                        <pre class="small mt-2 p-2 rounded" style="white-space:pre-wrap;background:rgba(0,0,0,0.18);max-height:360px;overflow:auto;">${escapeHtml(JSON.stringify(result, null, 2))}</pre>
                                    </details>
                                ` : ''}
                            </div>
                        </div>
                    `;
                    }).join('');
                } catch (error) {
                    console.error('Error loading actions:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load actions.</div>';
                }
            }

            async function queueDeviceAction(actionName, options = {}) {
                if (!selectedDeviceId) return;
                const shouldConfirm = options.confirm !== false;
                const shouldRefreshActions = options.refreshActions !== false;
                const shouldNotify = options.notify !== false;
                const labels = {
                    restart: 'restart',
                    update: 'update',
                    collect_rom_metadata: 'collect ROM and system metadata',
                    collect_game_logs: 'collect Game Logs',
                    collect_emulator_configs: 'collect emulator configs',
                    collect_log_sources: 'collect log sources',
                };
                if (shouldConfirm && !window.confirm(`Queue ${labels[actionName] || actionName} for this Drone?`)) return;
                try {
                    const response = await fetch(`/api/devices/${selectedDeviceId}/actions`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify({ action: actionName })
                    });
                    if (response.status === 401) {
                        logout();
                        showMessage('Session expired. Please log in again.', 'error');
                        throw new Error('Unauthorized');
                    }
                    if (!response.ok) throw new Error('Failed to queue action');
                    const data = await response.json().catch(() => ({}));
                    if (shouldRefreshActions) await loadDeviceActions();
                    if (shouldNotify) showMessage('Action queued.', 'success');
                    return data;
                } catch (error) {
                    console.error('Error queuing action:', error);
                    if (shouldNotify) showMessage('Failed to queue action.', 'danger');
                    throw error;
                }
            }

            function formatActionName(actionName) {
                const labels = {
                    collect_game_logs: 'Game Logs',
                    collect_emulator_configs: 'Emulator Configs',
                    collect_log_sources: 'Log Sources',
                    collect_rom_metadata: 'ROM Metadata',
                };
                return labels[actionName] || String(actionName || 'n/a').replaceAll('_', ' ');
            }

            function summarizeActionResult(result) {
                if (!result) return '';
                if (result.type === 'rom_metadata') return `${(result.systems || []).length} systems, ${(result.roms || []).length} ROM entries, ${(result.gamelists || []).length} gamelist.xml files`;
                if (result.type === 'game_logs') return `${(result.sessions || []).length} parsed play sessions, ${(result.logs || []).length} logs`;
                if (result.type === 'emulator_configs') return `${(result.configs || []).length} config files`;
                if (result.type === 'log_sources') return `${(result.logs || []).length} log sources`;
                return 'Data returned from Drone';
            }

            function escapeHtml(value) {
                return String(value ?? '')
                    .replace(/&/g, '&')
                    .replace(/</g, '<')
                    .replace(/>/g, '>')
                    .replace(/"/g, '"')
                    .replace(/'/g, '&#39;');
            }

            async function deleteSelectedDevice() {
                if (!selectedDeviceId) return;
                const current = currentDevices.find(d => d.device_id === selectedDeviceId);
                const label = current ? current.device_name : selectedDeviceId;
                if (!window.confirm(`Disconnect ${label}? This removes the Drone from this Overlord and it will no longer be controllable.`)) return;
                try {
                    const response = await apiDelete(`/api/devices/${selectedDeviceId}`);
                    if (!response.ok) throw new Error('Failed to delete device');
                    selectedDeviceId = null;
                    currentDeviceView = 'systems';
                    await loadDevices();
                    setRoute('devices', null, 'systems');
                    showMessage('Drone disconnected.', 'success');
                } catch (error) {
                    console.error('Error deleting device:', error);
                }
            }

            function setPageChrome(tabName) {
                const meta = pageMeta[tabName] || pageMeta.devices;
                const title = document.getElementById('page-title');
                const subtitle = document.getElementById('page-subtitle');
                if (title) title.textContent = meta[0];
                if (subtitle) subtitle.textContent = meta[1];
            }

            function activateNav(tabName) {
                document.querySelectorAll('.nav-btn, .sub-nav-btn').forEach(btn => btn.classList.remove('active'));
                const btn = document.querySelector(`.nav-btn[data-tab="${tabName}"], .sub-nav-btn[data-tab="${tabName}"]`);
                if (btn) btn.classList.add('active');
            }

            function switchTab(tabName, buttonEl = null, updateUrl = true) {
                activateNav(tabName);
                document.querySelectorAll('.dashboard-tab').forEach(section => { section.style.display = 'none'; });
                const tabMap = {
                    devices: 'devices-tab',
                    hive: 'hive-tab',
                    profile: 'profile-tab',
                    help: 'help-tab',
                };
                const tabElement = document.getElementById(tabMap[tabName]);
                if (tabElement) tabElement.style.display = 'block';
                currentTab = tabName;
                if (tabName === 'devices') {
                    updateSelectedDeviceWorkspace();
                    startDevicesPolling();
                } else {
                    stopDevicesPolling();
                }
                if (tabName === 'profile') renderProfileUI();
                if (tabName === 'hive') loadHive();
                updateSelectedDeviceSummary();
                applyRbacUI();
                setPageChrome(tabName);
                updateSharedSwarmNavButton();
                if (updateUrl) setRoute(tabName);
            }

            function showTokenModal(tokenValue, title = 'Drone Authorization Token') {
                const hidden = document.getElementById('token-modal-overlay');
                if (hidden) hidden.remove();
                const overlay = document.createElement('div');
                overlay.id = 'token-modal-overlay';
                overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:99999;display:flex;align-items:center;justify-content:center;';
                overlay.innerHTML = `
                  <div style="background:var(--admin-surface,#151f32);border:1px solid var(--admin-border,#31405f);border-radius:0.75rem;max-width:600px;width:90%;padding:1.5rem;box-shadow:0 1rem 3rem rgba(0,0,0,0.45);">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
                      <h4 style="margin:0;color:var(--admin-text,#ecf6ff);">${escapeHtml(title)}</h4>
                      <button onclick="this.closest('#token-modal-overlay').remove()" style="background:transparent;border:none;color:var(--admin-muted,#9fb0c9);font-size:1.5rem;cursor:pointer;">&times;</button>
                    </div>
                    <p style="color:var(--admin-muted,#9fb0c9);margin-bottom:0.75rem;">Copy this token and paste it into the Drone admin page. It is shown only once.</p>
                    <div style="display:flex;gap:0.5rem;">
                      <input id="token-modal-value" type="text" readonly value="${escapeHtml(tokenValue)}" style="flex:1;font-family:monospace;background:rgba(0,0,0,0.3);border:1px solid var(--admin-border,#31405f);color:var(--admin-text,#ecf6ff);padding:0.65rem;border-radius:0.35rem;font-size:0.85rem;">
                      <button id="token-copy-button" type="button" aria-label="Copy token" title="Copy token" onclick="copyTokenFromModal()" style="background:var(--admin-sidebar-accent,#00c2ff);border:none;color:#06111f;font-weight:800;padding:0.65rem 1rem;border-radius:0.35rem;cursor:pointer;white-space:nowrap;"><i class="bi bi-clipboard"></i></button>
                    </div>
                    <div id="token-copy-status" class="small mt-2" role="status" aria-live="polite" style="color:var(--admin-muted,#9fb0c9);min-height:1.25rem;"></div>
                    <div style="margin-top:1rem;text-align:right;">
                      <button onclick="this.closest('#token-modal-overlay').remove()" style="background:rgba(255,255,255,0.08);border:1px solid var(--admin-border,#31405f);color:var(--admin-text,#ecf6ff);padding:0.5rem 1rem;border-radius:0.35rem;cursor:pointer;">Close</button>
                    </div>
                  </div>
                `;
                document.body.appendChild(overlay);
            }

            async function copyTextToClipboard(text) {
                if (!text) throw new Error('No token available to copy.');
                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(text);
                    return;
                }
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.setAttribute('readonly', '');
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                document.body.appendChild(textarea);
                textarea.select();
                const ok = document.execCommand('copy');
                document.body.removeChild(textarea);
                if (!ok) throw new Error('Fallback clipboard copy failed.');
            }

            async function copyTokenFromModal() {
                const input = document.getElementById('token-modal-value');
                const button = document.getElementById('token-copy-button');
                const status = document.getElementById('token-copy-status');
                const original = '<i class="bi bi-clipboard"></i>';
                try {
                    await copyTextToClipboard(input ? input.value : '');
                    if (button) {
                        button.innerHTML = '<i class="bi bi-check2"></i>';
                        button.title = 'Copied';
                    }
                    if (status) {
                        status.textContent = 'Copied';
                        status.style.color = 'var(--admin-accent-green,#34d399)';
                    }
                    setTimeout(() => {
                        if (button) {
                            button.innerHTML = original;
                            button.title = 'Copy token';
                        }
                        if (status) status.textContent = '';
                    }, 2000);
                } catch (error) {
                    console.error('Token copy failed:', error);
                    if (status) {
                        status.textContent = error.message || 'Copy failed';
                        status.style.color = '#ff9aa7';
                    }
                    showMessage(error.message || 'Copy failed', 'error');
                }
            }

            function showMessage(message, type) {
                const msgElement = document.getElementById('auth-message');
                msgElement.textContent = message;
                msgElement.className = `message ${type}`;
                setTimeout(() => {
                    msgElement.classList.remove('success', 'error');
                }, 5000);
            }
