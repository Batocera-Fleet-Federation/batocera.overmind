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

    function dismissFeedbackBanner() {
      const banner = document.getElementById('feedbackBanner');
      if (banner) banner.style.display = 'none';
      try { localStorage.setItem('feedback_banner_dismissed', '1'); } catch (e) {}
    }

    function initFeedbackBanner() {
      const banner = document.getElementById('feedbackBanner');
      if (!banner) return;
      let dismissed = false;
      try { dismissed = localStorage.getItem('feedback_banner_dismissed') === '1'; } catch (e) {}
      banner.style.display = dismissed ? 'none' : 'flex';
    }

    function recordLandingVisit() {
      // Fire-and-forget: log this anonymous landing-page visit (server dedups by IP).
      try {
        fetch('/api/landing-visit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
      } catch (e) {}
    }

    // Stamp each `table.bff-stack` cell with the column header text so the CSS can render
    // a label:value stacked card per row on phone widths (see overmind.css .bff-stack).
    function decorateStackTables(root) {
      const scope = root || document;
      scope.querySelectorAll('table.bff-stack').forEach((table) => {
        const headers = Array.from(table.querySelectorAll('thead th')).map((th) => th.textContent.trim());
        if (!headers.length) return;
        table.querySelectorAll('tbody tr').forEach((tr) => {
          Array.from(tr.children).forEach((td, index) => {
            if (td.colSpan && td.colSpan > 1) return; // full-width/empty-state rows
            if (index < headers.length && !td.hasAttribute('data-label')) {
              td.setAttribute('data-label', headers[index]);
            }
          });
        });
      });
    }

    function setupStackTables() {
      const target = document.getElementById('app') || document.body;
      if (!target) return;
      let scheduled = false;
      const observer = new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => {
          scheduled = false;
          decorateStackTables(target);
        });
      });
      observer.observe(target, { childList: true, subtree: true });
      decorateStackTables(target);
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
            let currentDeviceView = 'overview';
            let routeSwarmId = null;
            let currentDeviceSystems = {};
            let currentDeviceSystemsPage = { total: 0, page: 1, per_page: 100 };
            let currentSystemRomPages = {};
            let systemRomRequestSeq = {};
            let currentBiosFilePage = { bios: [], total: 0, nextOffset: 0, loading: false, error: false };
            let currentBiosSummary = { total: 0, loading: false, error: false };
            // Per-system BIOS pagination (the "BIOS" category under each system, filtered to
            // that system's known BIOS files) -- distinct from currentBiosFilePage, which
            // backs the top-level "Shared / Unassigned BIOS" root.
            let currentSystemBiosPages = {};
            let selectedSystemName = null;
            let selectedFileCategory = null;
            let deviceRomSearchQuery = '';
            // 'mine' (default, this drone's own files) | 'all' (fleet-wide) | 'missing'
            // (fleet-wide, not present on this drone) -- replaces the old "Show all
            // Drones" toggle, applied uniformly to both the ROM and BIOS tree leaves.
            let deviceAssetScope = 'mine';
            let deviceSystemsPage = 1;
            let swarmMasterPage = 1;
            let pendingConnectionTimer = null;
            let actionRefreshTimer = null;
            let selectedDeviceDataRefreshTimer = null;
            let uiLoadingRequests = 0;
            let uiLoadingTimer = null;
            let loadingToastEl = null;
            let devicesRefreshTimer = null;
            let devicesRefreshInFlight = false;
            let renderedDeviceActionsDeviceId = null;
            let renderedDeviceActionsSignature = null;
            let pendingDeviceAutomation = {};
            let pendingConnectionsInFlight = false;
            let downloadsRefreshTimer = null;
            let downloadsRefreshInFlight = false;
            let notificationsInFlight = false;
            let superAdminMetricsTimer = null;
            let syncActionsOffset = 0;
            let syncActionsPageSize = 20;
            let auditLogOffset = 0;
            let auditLogPageSize = 20;
            let visitorsOffset = 0;
            let visitorsPageSize = 20;
            let transfersOffset = 0;
            let transfersPageSize = 50;
            let transfersStatusFilter = '';
            let notificationsRefreshTimer = null;
            let notificationsPageOffset = 0;
            const notificationsPageSize = 25;
            let inactivityTimer = null;
            let lastAuthRefreshAt = 0;
            let authRefreshInFlight = false;
            let logoutNoticeShown = false;
            let pendingInvitationToken = sessionStorage.getItem('pending_invitation_token') || null;
            const INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000;
            const AUTH_REFRESH_INTERVAL_MS = 2 * 60 * 1000;
            const SUPER_ADMIN_EMAIL = 'mr_jerrodh@hotmail.com';
            const SYSTEMS_FETCH_PAGE_SIZE = 100;
            const TREE_FILE_LOAD_SIZE = 10;
            const BIOS_TREE_ROOT = '__bios__';
            const pageMeta = {
                auth: ['Overlord Login', 'Access the Overmind'],
                devices: ['My Swarm', 'Systems and ROMs'],
                hive: ['The Hive', 'Browse public swarm listings'],
                profile: ['Profile', 'Account, access, and preferences'],
                notifications: ['Notifications', 'Swarm event history'],
                'super-admin': ['Super Admin', 'Users, swarms, and drones'],
                help: ['Help', 'Install, connect, and configure Drones'],
            };

            document.addEventListener('DOMContentLoaded', async () => {
                initFeedbackBanner();
                setupInactivityTracking();
                setupStackTables();
                setupAccountMenu();
                setupNotificationMenu();
	                loadAuthProviders();
	                handleOAuthReturn();
	                handleAuthHashActions();
                const token = localStorage.getItem('auth_token');
                if (!token) recordLandingVisit();
                if (token) {
	                    authToken = token;
                    routeSwarmId = parseRoute().swarmId || null;
	                    showDashboard();
                    try {
	                    await loadSwarms();
	                    loadProfile();
                        loadDevices();
                        loadPendingConnections();
                        loadNotifications();
                    } catch (error) {
                        console.error('Error restoring session:', error);
                        logout('Session expired. Please log in again.', '#/login');
                    }
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
            function markAuthenticatedSession() {
                logoutNoticeShown = false;
            }
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
            async function handleApiAuthFailure(response) {
                if (response.status === 401) {
                    logout('Session expired. Please log in again.', '#/login');
                    throw new Error('Unauthorized');
                }
                if (response.status === 403) {
                    const payload = await response.clone().json().catch(() => ({}));
                    const detail = String(payload.detail || '').toLowerCase();
                    if (detail.includes('email verification')) {
                        logout('Email verification required. Please verify your account and log in again.', '#/login');
                        throw new Error('Forbidden');
                    }
                }
            }

            function ensureToastContainer() {
                let container = document.getElementById('toast-alert-container');
                if (!container) {
                    container = document.createElement('div');
                    container.id = 'toast-alert-container';
                    container.className = 'toast-alert-container';
                    container.setAttribute('aria-live', 'polite');
                    document.body.appendChild(container);
                }
                return container;
            }

            function dismissToast(toast) {
                if (toast && toast.parentNode) toast.parentNode.removeChild(toast);
                if (loadingToastEl === toast) loadingToastEl = null;
            }

            function showToast(message, type = 'success', durationMs = 5000) {
                const resolvedType = type === 'error' ? 'danger' : type;
                const icons = {
                    success: 'bi-check-circle-fill',
                    danger: 'bi-exclamation-triangle-fill',
                    warning: 'bi-exclamation-circle-fill',
                    info: 'bi-info-circle-fill',
                };
                const toast = document.createElement('div');
                toast.className = `toast-alert alert-${resolvedType}`;
                toast.setAttribute('role', resolvedType === 'danger' || resolvedType === 'warning' ? 'alert' : 'status');
                const icon = document.createElement('i');
                icon.className = `bi ${icons[resolvedType] || icons.info}`;
                const text = document.createElement('span');
                text.textContent = String(message || '');
                toast.appendChild(icon);
                toast.appendChild(text);
                ensureToastContainer().appendChild(toast);
                if (durationMs > 0) window.setTimeout(() => dismissToast(toast), durationMs);
                return toast;
            }

            function showLoadingToast(message = 'Loading...') {
                if (loadingToastEl) return;
                loadingToastEl = document.createElement('div');
                loadingToastEl.className = 'toast-alert alert-loading';
                loadingToastEl.setAttribute('role', 'status');
                const spinner = document.createElement('span');
                spinner.className = 'loading';
                spinner.setAttribute('aria-hidden', 'true');
                const text = document.createElement('span');
                text.textContent = message;
                loadingToastEl.appendChild(spinner);
                loadingToastEl.appendChild(text);
                ensureToastContainer().appendChild(loadingToastEl);
            }

            function hideLoadingToast() {
                dismissToast(loadingToastEl);
            }

            function beginUiLoading(message = 'Loading...') {
                uiLoadingRequests += 1;
                if (uiLoadingRequests !== 1) return;
                uiLoadingTimer = setTimeout(() => {
                    if (uiLoadingRequests > 0) showLoadingToast(message);
                }, 120);
            }

            function endUiLoading() {
                uiLoadingRequests = Math.max(0, uiLoadingRequests - 1);
                if (uiLoadingRequests > 0) return;
                if (uiLoadingTimer) {
                    clearTimeout(uiLoadingTimer);
                    uiLoadingTimer = null;
                }
                hideLoadingToast();
            }

	            async function apiGet(path, options = {}) {
                const showLoader = options.showLoader !== false;
                const loadingMessage = options.loadingMessage || loadingMessageForPath(path);
                const timeoutMs = Math.max(1000, Number(options.timeoutMs || 20000));
                const controller = new AbortController();
                const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
                if (showLoader) beginUiLoading(loadingMessage);
                try {
                    const response = await fetch(path, {
                        headers: { 'Authorization': `Bearer ${authToken}` },
                        signal: controller.signal,
                    });
                    await handleApiAuthFailure(response);
                    return response;
                } finally {
                    window.clearTimeout(timeout);
                    if (showLoader) endUiLoading();
                }
	            }

            function loadingMessageForPath(path) {
                const cleanPath = String(path || '').split('?')[0];
                if (cleanPath.includes('/api/notifications')) return 'Loading notifications...';
                if (cleanPath.includes('/api/drone-connections')) return 'Loading Drone requests...';
                if (cleanPath.includes('/api/devices') && cleanPath.includes('/roms')) return 'Loading ROMs...';
                if (cleanPath.includes('/api/devices') && cleanPath.includes('/systems')) return 'Loading systems...';
                if (cleanPath.includes('/api/devices') && cleanPath.includes('/master-roms')) return 'Loading master ROMs...';
                if (cleanPath.includes('/api/devices') && cleanPath.includes('/master-bios')) return 'Loading master BIOS...';
                if (cleanPath.includes('/api/devices') && cleanPath.includes('/master-artwork')) return 'Loading artwork...';
                if (cleanPath.includes('/api/devices') && cleanPath.includes('/gamelogs')) return 'Loading game logs...';
                if (cleanPath.includes('/api/devices') && cleanPath.includes('/actions')) return 'Loading actions...';
                if (cleanPath.includes('/api/devices')) return 'Loading devices...';
                if (cleanPath.includes('/api/downloads')) return 'Loading downloads...';
                if (cleanPath.includes('/api/master-roms')) return 'Loading master ROMs...';
                if (cleanPath.includes('/api/systems')) return 'Loading systems...';
                if (cleanPath.includes('/api/hive')) return 'Loading hive...';
                if (cleanPath.includes('/api/profile')) return 'Loading profile...';
                if (cleanPath.includes('/api/swarms')) return 'Loading swarms...';
                if (cleanPath.includes('/api/admin/runtime-metrics')) return 'Loading runtime metrics...';
                if (cleanPath.includes('/api/admin/runtime-logs')) return 'Loading runtime logs...';
                if (cleanPath.includes('/api/admin/overview')) return 'Loading admin overview...';
                return 'Loading...';
            }

	            function withSwarm(path) {
	                if (!selectedSwarmId) return path;
	                const separator = path.includes('?') ? '&' : '?';
	                return `${path}${separator}swarm_id=${encodeURIComponent(selectedSwarmId)}`;
	            }

            async function apiPatch(path, payload) {
                beginUiLoading();
                try {
                    const response = await fetch(path, {
                        method: 'PATCH',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify(payload)
                    });
                    await handleApiAuthFailure(response);
                    return response;
                } finally {
                    endUiLoading();
                }
            }

            async function apiPost(path, payload) {
                beginUiLoading();
                try {
                    const response = await fetch(path, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify(payload || {})
                    });
                    await handleApiAuthFailure(response);
                    return response;
                } finally {
                    endUiLoading();
                }
            }

            async function apiDelete(path) {
                beginUiLoading();
                try {
                    const response = await fetch(path, {
                        method: 'DELETE',
                        headers: { 'Authorization': `Bearer ${authToken}` }
                    });
                    await handleApiAuthFailure(response);
                    return response;
                } finally {
                    endUiLoading();
                }
            }

            function renderNotifications(payload = {}) {
                const rows = payload.notifications || [];
                const unread = Number(payload.unread_count || 0);
                const badge = document.getElementById('notification-badge');
                const list = document.getElementById('notification-list');
                if (badge) {
                    badge.textContent = unread > 99 ? '99+' : String(unread);
                    badge.style.display = unread > 0 ? '' : 'none';
                    badge.setAttribute('aria-label', `${unread} unread notifications`);
                }
                if (!list) return;
                if (!rows.length) {
                    list.innerHTML = '<div class="empty-state">No notifications yet.</div>';
                    return;
                }
                list.innerHTML = rows.slice(0, 5).map(row => {
                    const created = row.created_at ? new Date(row.created_at).toLocaleString() : '';
                    const swarm = row.swarm_name ? `<span>${escapeHtml(row.swarm_name)}</span>` : '';
                    return `
                        <div class="notification-item ${row.read ? '' : 'unread'}">
                            <div class="notification-title">${escapeHtml(row.title || 'Notification')}</div>
                            <div class="small mt-1">${escapeHtml(row.short_description || row.title || '')}</div>
                            <div class="notification-meta">${[created, swarm].filter(Boolean).join(' · ')}</div>
                        </div>
                    `;
                }).join('');
            }

            async function loadNotifications(options = {}) {
                if (!authToken) return;
                if (notificationsInFlight) return;
                notificationsInFlight = true;
                try {
                    // Dropdown shows only the latest few; the badge count is authoritative
                    // (server returns total unread regardless of page size).
                    const response = await apiGet('/api/notifications?limit=5', { showLoader: options.showLoader !== false });
                    if (!response.ok) throw new Error('Failed to load notifications');
                    renderNotifications(await response.json());
                } catch (error) {
                    console.error('Error loading notifications:', error);
                } finally {
                    notificationsInFlight = false;
                }
            }

            function toggleNotificationsPanel() {
                const panel = document.getElementById('notification-panel');
                if (!panel) return;
                const shouldShow = panel.style.display === 'none' || !panel.style.display;
                panel.style.display = shouldShow ? 'flex' : 'none';
                if (shouldShow) loadNotifications();
            }

            function closeNotificationsPanel() {
                const panel = document.getElementById('notification-panel');
                if (panel) panel.style.display = 'none';
            }

            function setupNotificationMenu() {
                document.addEventListener('click', event => {
                    const menu = document.getElementById('notification-menu');
                    const panel = document.getElementById('notification-panel');
                    if (!menu || !panel || panel.style.display === 'none') return;
                    if (!menu.contains(event.target)) closeNotificationsPanel();
                });
            }

            function openNotificationsPage() {
                closeNotificationsPanel();
                switchTab('notifications');
            }

            async function markNotificationsRead() {
                if (!authToken) return;
                try {
                    const response = await fetch('/api/notifications/read', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify({})
                    });
                    await handleApiAuthFailure(response);
                    if (!response.ok) throw new Error('Failed to mark notifications read');
                    await loadNotifications();
                    if (currentTab === 'notifications') await loadNotificationsPage();
                } catch (error) {
                    console.error('Error marking notifications read:', error);
                }
            }

            async function dismissNotification(notificationId) {
                if (!authToken || !notificationId) return;
                try {
                    const response = await fetch('/api/notifications/dismiss', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify({ ids: [notificationId] })
                    });
                    await handleApiAuthFailure(response);
                    if (!response.ok) throw new Error('Failed to dismiss notification');
                    await loadNotifications();
                    await loadNotificationsPage();
                } catch (error) {
                    console.error('Error dismissing notification:', error);
                    showMessage('Failed to dismiss notification.', 'error');
                }
            }

            async function dismissAllNotifications() {
                if (!authToken) return;
                if (!window.confirm('Dismiss all notifications?')) return;
                try {
                    const response = await fetch('/api/notifications/dismiss', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify({})
                    });
                    await handleApiAuthFailure(response);
                    if (!response.ok) throw new Error('Failed to dismiss notifications');
                    await loadNotifications();
                    await loadNotificationsPage();
                    showMessage('Notifications dismissed.', 'success');
                } catch (error) {
                    console.error('Error dismissing notifications:', error);
                    showMessage('Failed to dismiss notifications.', 'error');
                }
            }

            function gotoNotificationsPage(offset) {
                // Pager entry point: reflect the offset in the URL (so Back/refresh
                // restore it) and reset scroll, then load the page.
                const safe = Math.max(0, Number(offset) || 0);
                updateRouteQuery({ np: safe > 0 ? safe : null });
                loadNotificationsPage(safe);
                scrollAppToTop();
            }

            async function loadNotificationsPage(offset = notificationsPageOffset) {
                const container = document.getElementById('notifications-page-content');
                if (!container || !authToken) return;
                notificationsPageOffset = Math.max(0, Number(offset) || 0);
                container.innerHTML = '<div class="empty-state">Loading notifications...</div>';
                try {
                    // Page through the database with LIMIT/OFFSET instead of pulling every
                    // notification, so the tab loads fast regardless of history size.
                    const response = await apiGet(`/api/notifications?limit=${notificationsPageSize}&offset=${notificationsPageOffset}`);
                    if (!response.ok) throw new Error('Failed to load notifications');
                    const data = await response.json();
                    const rows = data.notifications || [];
                    const total = Number(data.total_count || 0);
                    if (!rows.length) {
                        // An out-of-range page (e.g. after dismissals) snaps back to the first.
                        if (notificationsPageOffset > 0) { return loadNotificationsPage(0); }
                        container.innerHTML = '<div class="empty-state">No notifications to show.</div>';
                        return;
                    }
                    const pageStart = notificationsPageOffset + 1;
                    const pageEnd = notificationsPageOffset + rows.length;
                    const hasPrev = notificationsPageOffset > 0;
                    const hasNext = pageEnd < total;
                    const pager = `
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mt-2">
                            <div class="small text-muted">Showing ${pageStart}–${pageEnd} of ${total}</div>
                            <div class="btn-group">
                                <button class="btn btn-outline-secondary btn-sm" type="button" ${hasPrev ? '' : 'disabled'} onclick="gotoNotificationsPage(${Math.max(0, notificationsPageOffset - notificationsPageSize)})"><i class="bi bi-chevron-left"></i> Previous</button>
                                <button class="btn btn-outline-secondary btn-sm" type="button" ${hasNext ? '' : 'disabled'} onclick="gotoNotificationsPage(${notificationsPageOffset + notificationsPageSize})">Next <i class="bi bi-chevron-right"></i></button>
                            </div>
                        </div>`;
                    container.innerHTML = `
                        <div class="table-responsive">
                            <table class="table table-sm align-middle notifications-table bff-stack">
                                <thead>
                                    <tr>
                                        <th>Status</th>
                                        <th>Notification</th>
                                        <th>Swarm</th>
                                        <th>Time</th>
                                        <th></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    ${rows.map(row => {
                                        const created = row.created_at ? new Date(row.created_at).toLocaleString() : '';
                                        const fullDescription = row.full_description || row.message || '';
                                        const shortDescription = row.short_description || row.title || '';
                                        const detailId = `notification-detail-${cssSafeId(row.id || '')}`;
                                        return `<tr class="${row.read ? '' : 'table-active'} notification-row" role="button" onclick="toggleElement('${escapeHtml(detailId)}')">
                                            <td><span class="badge ${row.read ? 'text-bg-secondary' : 'text-bg-primary'}">${row.read ? 'Read' : 'New'}</span></td>
                                            <td>
                                                <div class="fw-bold">${escapeHtml(row.title || 'Notification')}</div>
                                                <div class="small">${escapeHtml(shortDescription)}</div>
                                                <div id="${escapeHtml(detailId)}" class="small text-muted mt-2" style="display:none">${escapeHtml(fullDescription)}</div>
                                                <div class="small text-muted">${escapeHtml(row.event_type || '')}</div>
                                            </td>
                                            <td class="small">${escapeHtml(row.swarm_name || '')}</td>
                                            <td class="small text-muted">${escapeHtml(created)}</td>
                                            <td class="text-end">
                                                <button class="btn btn-outline-danger btn-sm" type="button" onclick="event.stopPropagation(); dismissNotification('${escapeHtml(row.id || '')}')">Dismiss</button>
                                            </td>
                                        </tr>`;
                                    }).join('')}
                                </tbody>
                            </table>
                        </div>
                        ${pager}
                    `;
                } catch (error) {
                    console.error('Error loading notifications page:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load notifications.</div>';
                }
            }

            function startNotificationPolling() {
                if (notificationsRefreshTimer) clearInterval(notificationsRefreshTimer);
                notificationsRefreshTimer = setInterval(() => loadNotifications({showLoader: false}), 60000);
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
                markAuthenticatedSession();
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
	                } else if (hash.startsWith('#oauth_error=')) {
	                    const params = new URLSearchParams(hash.slice(1));
	                    showLandingAuth('login-form');
	                    showMessage(params.get('oauth_error') || 'Social login failed. Please try again.', 'error');
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
                        markAuthenticatedSession();
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
                    await loadNotifications();
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
                const username = document.getElementById('register-username').value.trim();
                const password = document.getElementById('register-password').value;
                const btn = e.target.querySelector('button');
                btn.disabled = true;
                try {
                    const response = await fetch('/api/auth/register', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email, username, password, invitation_token: pendingInvitationToken })
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

            async function handleResendVerificationCode(e) {
                const btn = e && e.target ? e.target : document.getElementById('resend-verification-btn');
                const email = document.getElementById('verify-email').value.trim();
                if (!email) {
                    showMessage('Enter your email address first.', 'error');
                    return;
                }
                if (btn) btn.disabled = true;
                try {
                    const response = await fetch('/api/auth/resend-verification', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email })
                    });
                    if (!response.ok) throw new Error('Unable to resend code');
                    document.getElementById('verify-code').value = '';
                    showMessage('A new validation code has been sent. Older codes are no longer valid.', 'success');
                } catch (error) {
                    showMessage(error.message || 'Unable to resend code.', 'error');
                } finally {
                    if (btn) btn.disabled = false;
                }
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
                    logout('You were logged out after 30 minutes of inactivity.', '#/login');
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
                    } else if (response.status === 401 || response.status === 403) {
                        logout('Session expired. Please log in again.', '#/login');
                    }
                } catch (error) {
                    console.warn('Auth refresh failed; keeping current token until backend validation requires login.');
                } finally {
                    authRefreshInFlight = false;
                }
            }

	            function logout(message = null, nextHash = '#/home') {
                const shouldShowLogoutMessage = Boolean(message) && !logoutNoticeShown;
                if (message) logoutNoticeShown = true;
                closeAccountMenu();
                closeNotificationsPanel();
                authToken = null;
                currentUser = null;
                currentProfile = null;
                pendingConnections = [];
                selectedDeviceId = null;
                currentDeviceView = 'overview';
                lastAuthRefreshAt = 0;
                if (pendingConnectionTimer) clearInterval(pendingConnectionTimer);
                if (actionRefreshTimer) clearInterval(actionRefreshTimer);
                if (selectedDeviceDataRefreshTimer) clearInterval(selectedDeviceDataRefreshTimer);
                if (devicesRefreshTimer) clearInterval(devicesRefreshTimer);
                if (downloadsRefreshTimer) clearInterval(downloadsRefreshTimer);
                if (notificationsRefreshTimer) clearInterval(notificationsRefreshTimer);
                stopInactivityTimer();
                pendingConnectionTimer = null;
                actionRefreshTimer = null;
                selectedDeviceDataRefreshTimer = null;
                devicesRefreshTimer = null;
                downloadsRefreshTimer = null;
                notificationsRefreshTimer = null;
                const notificationPanel = document.getElementById('notification-panel');
                if (notificationPanel) notificationPanel.style.display = 'none';
                const notificationBadge = document.getElementById('notification-badge');
                if (notificationBadge) notificationBadge.style.display = 'none';
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
                if (shouldShowLogoutMessage) showMessage(message, 'error');
	            }

	            function showDashboard() {
                hideLandingShell();
                document.body.classList.add('is-authenticated');
                updateSuperAdminVisibility();
                document.getElementById('auth-section').classList.remove('active');
                document.getElementById('dashboard-section').classList.add('active');
                document.getElementById('auth-section').style.display = 'none';
                document.getElementById('dashboard-section').style.display = 'block';
                setPageChrome(currentTab);
                startPendingConnectionPolling();
                startNotificationPolling();
                resetInactivityTimer();
            }

            function startPendingConnectionPolling() {
                if (pendingConnectionTimer) clearInterval(pendingConnectionTimer);
                pendingConnectionTimer = setInterval(() => loadPendingConnections({showLoader: false}), 30000);
            }

            function startDevicesPolling() {
                if (devicesRefreshTimer) return;
                devicesRefreshTimer = setInterval(() => {
                    // only poll when on devices tab
                    if (currentTab === 'devices' && document.getElementById('devices-tab')?.style.display !== 'none') {
                        loadDevices({showLoader: false, background: true});
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
	                if (!response.ok) throw new Error('Failed to load swarms');
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
                    currentDeviceView = 'overview';
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
                    currentDeviceView = 'overview';
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
                    updateSuperAdminVisibility();
                    renderProfileUI();
                } catch (error) {
                    console.error('Error loading profile:', error);
                }
            }

            function isSuperAdmin() {
                const email = String((currentUser && currentUser.email) || (currentProfile && currentProfile.email) || '').trim().toLowerCase();
                return email === SUPER_ADMIN_EMAIL;
            }

            function updateSuperAdminVisibility() {
                document.querySelectorAll('.super-admin-only').forEach(node => {
                    node.style.display = isSuperAdmin() ? '' : 'none';
                });
            }

            function setupAccountMenu() {
                document.addEventListener('click', event => {
                    const menu = document.getElementById('account-menu');
                    if (menu && menu.open && !menu.contains(event.target)) menu.removeAttribute('open');
                });
                document.addEventListener('keydown', event => {
                    if (event.key === 'Escape') closeAccountMenu();
                });
            }

            function closeAccountMenu() {
                const menu = document.getElementById('account-menu');
                if (menu) menu.removeAttribute('open');
            }

            function renderAccountAvatar() {
                const avatar = document.getElementById('nav-profile-avatar');
                const fallback = document.getElementById('nav-profile-avatar-fallback');
                if (!avatar || !fallback) return;
                const avatarDataUrl = currentProfile && currentProfile.avatar_data_url ? currentProfile.avatar_data_url : '';
                if (avatarDataUrl) {
                    avatar.src = avatarDataUrl;
                    avatar.style.display = 'block';
                    fallback.style.display = 'none';
                    avatar.onerror = () => {
                        avatar.style.display = 'none';
                        fallback.style.display = 'inline-flex';
                    };
                    return;
                }
                avatar.removeAttribute('src');
                avatar.style.display = 'none';
                fallback.style.display = 'inline-flex';
            }

            function ensureProfileState() {
                if (currentProfile) return currentProfile;
                currentProfile = {
                    id: currentUser && currentUser.id ? currentUser.id : null,
                    email: currentUser && currentUser.email ? currentUser.email : '',
                    username: currentUser && currentUser.username ? currentUser.username : '',
                    full_name: currentUser && currentUser.full_name ? currentUser.full_name : '',
                    avatar_data_url: '',
                    fleet_settings: {},
                    notification_settings: {}
                };
                return currentProfile;
            }

            function renderProfileUI() {
                if (!currentProfile) return;
                renderAccountAvatar();
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
                const types = ns.types || {};
                document.querySelectorAll('.notify-type-checkbox').forEach(input => {
                    input.checked = types[input.dataset.notifyType] !== false;
                });
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
                document.querySelectorAll('.notify-type-checkbox').forEach(input => {
                    input.disabled = !slackEnabled && !discordEnabled && !emailEnabled;
                });
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
                    const avatarDataUrl = canvas.toDataURL('image/png');
                    ensureProfileState().avatar_data_url = avatarDataUrl;
                    renderProfileUI();
                    overlay.remove();
                    saveProfile(avatarDataUrl);
                };
            }

            async function saveProfile(avatarDataUrlOverride = null) {
                try {
                    const profile = ensureProfileState();
                    const usernameInput = document.getElementById('profile-username-input');
                    const payload = {
                        username: usernameInput ? usernameInput.value.trim() || null : profile.username || null,
                    };
                    const avatarValue = avatarDataUrlOverride !== null ? avatarDataUrlOverride : profile.avatar_data_url;
                    if (avatarValue) payload.avatar_data_url = avatarValue;
                    const response = await apiPatch('/api/profile', payload);
                    if (!response.ok) throw new Error('Failed to save profile');
                    currentProfile = await response.json();
                    const swarmNameInput = document.getElementById('profile-swarm-name-input');
                    const swarmName = swarmNameInput ? swarmNameInput.value.trim() : '';
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
                        <div class="table-responsive"><table class="table table-sm align-middle bff-stack">
                            <thead><tr><th>Email</th><th>Role</th><th>Registration</th><th>Status</th><th></th></tr></thead>
                            <tbody>
                                ${members.map(m => `<tr><td>${escapeHtml(m.email || '')}</td><td>${escapeHtml(roleLabel(m.role))}</td><td>registered</td><td>${escapeHtml(m.status || 'accepted')}</td><td>${canMutateSwarm() && m.role === 'overseer' ? `<button class="btn btn-outline-danger btn-sm" title="Remove this Overseer from the swarm" onclick="removeSwarmMember('${escapeHtml(m.user_id)}')"><i class="bi bi-person-x me-1"></i>Remove Overseer</button>` : ''}</td></tr>`).join('')}
                                ${invites.map(i => `<tr><td>${escapeHtml(i.email || '')}</td><td>${escapeHtml(roleLabel(i.role))}</td><td>${escapeHtml(i.registration_status || 'invited')}</td><td>${escapeHtml(i.status || 'pending')}</td><td>${canMutateSwarm() && i.status === 'pending' ? `<div class="d-flex gap-2 flex-wrap"><button class="btn btn-outline-primary btn-sm" title="Send a new Overseer invitation link" onclick="resendOverseerInvite('${escapeHtml(i.id)}')"><i class="bi bi-envelope-arrow-up me-1"></i>Resend Invite</button><button class="btn btn-outline-danger btn-sm" title="Remove this pending Overseer invitation" onclick="removePendingOverseerInvite('${escapeHtml(i.id)}')"><i class="bi bi-person-x me-1"></i>Remove Invite</button></div>` : ''}</td></tr>`).join('')}
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
                if (!selectedSwarmId || !window.confirm('Remove this Overseer from the swarm? They will lose access to this swarm immediately.')) return;
                const response = await apiDelete(`/api/swarms/${selectedSwarmId}/members/${userId}`);
                if (!response.ok) {
                    showMessage('Unable to remove Overseer.', 'error');
                    return;
                }
                await loadSwarmAccess();
                showMessage('Overseer removed from the swarm.', 'success');
            }

            async function resendOverseerInvite(invitationId) {
                if (!selectedSwarmId || !window.confirm('Resend this Overseer invitation? The previous invitation link will no longer work.')) return;
                try {
                    const response = await fetch(`/api/swarms/${selectedSwarmId}/invitations/${encodeURIComponent(invitationId)}/resend`, {
                        method: 'POST',
                        headers: {'Authorization': `Bearer ${authToken}`}
                    });
                    await handleApiAuthFailure(response);
                    if (!response.ok) throw new Error('Unable to resend invitation');
                    await loadSwarmAccess();
                    showMessage('Overseer invitation resent.', 'success');
                } catch (error) {
                    showMessage(error.message || 'Unable to resend invitation.', 'error');
                }
            }

            async function removePendingOverseerInvite(invitationId) {
                if (!selectedSwarmId || !window.confirm('Remove this pending Overseer invitation? The invitation link will no longer work.')) return;
                const response = await apiDelete(`/api/swarms/${selectedSwarmId}/invitations/${encodeURIComponent(invitationId)}`);
                if (!response.ok) {
                    showMessage('Unable to remove invitation.', 'error');
                    return;
                }
                await loadSwarmAccess();
                showMessage('Pending Overseer invitation removed.', 'success');
            }

            async function saveNotificationSettings() {
                try {
                    const selectedTypes = {};
                    document.querySelectorAll('.notify-type-checkbox').forEach(input => {
                        selectedTypes[input.dataset.notifyType] = input.checked;
                    });
                    const response = await apiPatch('/api/profile', {
                        notification_settings: {
                            notify_slack: document.getElementById('notify-slack').checked,
                            notify_discord: document.getElementById('notify-discord').checked,
                            notify_email: document.getElementById('notify-email').checked,
                            slack_webhook: document.getElementById('notify-slack-webhook').value.trim(),
                            discord_webhook: document.getElementById('notify-discord-webhook').value.trim(),
                            types: selectedTypes
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

            async function loadDevices(options = {}) {
                const applyRoute = options.applyRoute !== false;
                const background = options.background === true;
                if (devicesRefreshInFlight) return;
                devicesRefreshInFlight = true;
                try {
                    const response = await apiGet(withSwarm('/api/devices'), { showLoader: options.showLoader !== false });
                    if (!response.ok) throw new Error('Failed to load devices');
                    const data = await response.json();
                    currentDevices = (data.devices || []).map(applyPendingDeviceAutomation);
                    const selectedDeviceWasRemoved = Boolean(selectedDeviceId)
                        && !currentDevices.some(d => d.device_id === selectedDeviceId);
                    if (selectedDeviceWasRemoved) selectedDeviceId = null;
                    if (background) {
                        updateDevicesListInPlace();
                        updateSelectedDeviceSummary();
                        updateSelectedDeviceHeader();
                        updateDeviceAdminStatusInPlace();
                        if (selectedDeviceWasRemoved) {
                            updateSelectedDeviceWorkspace();
                            setRoute('devices', null, 'systems');
                        }
                        return;
                    }
                    displayDevices();
                    if (applyRoute) {
                        applyRouteFromHash();
                    } else {
                        updateSelectedDeviceSummary();
                        updateSelectedDeviceWorkspace();
                    }
                } catch (error) {
                    console.error('Error loading devices:', error);
                } finally {
                    devicesRefreshInFlight = false;
                }
            }

            async function loadPendingConnections(options = {}) {
                if (!authToken || !canMutateSwarm()) {
                    pendingConnections = [];
                    displayPendingConnections();
                    return;
                }
                if (pendingConnectionsInFlight) return;
                pendingConnectionsInFlight = true;
                try {
	                    const response = await apiGet(withSwarm('/api/drone-connections'), { showLoader: options.showLoader !== false });
                    if (!response.ok) throw new Error('Failed to load drone connections');
                    const data = await response.json();
                    pendingConnections = data.connections || [];
                    displayPendingConnections();
                } catch (error) {
                    console.error('Error loading drone connections:', error);
                } finally {
                    pendingConnectionsInFlight = false;
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
                            <button id="device-tile-${cssSafeId(device.device_id)}" type="button" class="card device-tile text-start border shadow-sm ${device.device_id === selectedDeviceId ? 'active' : ''}" onclick="selectDevice('${device.device_id}')">
                                <div class="card-body">
                                    <div class="d-flex align-items-start justify-content-between gap-2 mb-2">
                                        <h5 class="card-title mb-0" data-device-field="name">${device.device_name}</h5>
                                        <i class="bi bi-hdd-network text-muted"></i>
                                    </div>
                                    <div class="small text-muted mb-1" data-device-field="batocera">Batocera: ${escapeHtml((device.system_info || {}).batocera_version || 'n/a')}</div>
                                    <div class="small text-muted mb-3" data-device-field="counts">${escapeHtml(formatDeviceInventorySummary(device))}</div>
                                    <div class="mt-3 d-flex flex-wrap gap-1">
                                        <span class="badge ${device.online ? 'text-bg-success' : 'text-bg-danger'}" data-device-field="online">${device.online ? 'Online' : 'Offline'}</span>
                                        <span class="badge ${device.swarm_connected ? 'text-bg-success' : 'text-bg-secondary'}" data-device-field="connected">${device.swarm_connected ? 'Connected to Swarm' : 'Not Connected to Swarm'}</span>
                                        <span class="badge ${(device.public_reachability && device.public_reachability.resolvable) ? 'text-bg-success' : 'text-bg-secondary'}" data-device-field="resolvable" title="Overmind public reachability probe (TCP connect to the registered public IP)">${(device.public_reachability && device.public_reachability.resolvable) ? 'Resolvable' : 'Not Resolvable'}</span>
                                        <span class="badge ${device.edge_online ? 'text-bg-success' : 'text-bg-secondary'}" data-device-field="edge" title="Connected to Overmind via the outbound Edge link (no router port-forward required)">${device.edge_online ? 'Online via Edge' : 'Not on Edge'}</span>
                                    </div>
                                    <div class="small text-muted mt-3" data-device-field="last-seen">${device.last_seen ? `Last seen: ${new Date(device.last_seen).toLocaleString()}` : 'Last seen unavailable'}</div>
                                </div>
                            </button>
                        `).join('')}
                    </div>
                `;
            }

            function formatDeviceInventorySummary(device) {
                const hasRomCount = device && device.rom_count !== undefined && device.rom_count !== null;
                const hasGameCount = device && device.game_count !== undefined && device.game_count !== null;
                if (!hasRomCount && !hasGameCount) return 'Inventory: open Systems';
                const romCount = Number(device.rom_count || 0);
                const gameCount = Number(device.game_count ?? romCount);
                return `Games: ${gameCount.toLocaleString()} · ROM Files: ${romCount.toLocaleString()}`;
            }

            function updateDeviceTileBadge(tile, field, enabled, enabledText, disabledText, disabledClass) {
                const badge = tile.querySelector(`[data-device-field="${field}"]`);
                if (!badge) return;
                badge.textContent = enabled ? enabledText : disabledText;
                badge.classList.toggle('text-bg-success', enabled);
                badge.classList.toggle(disabledClass, !enabled);
                if (enabled) badge.classList.remove(disabledClass);
            }

            function updateDevicesListInPlace() {
                const container = document.getElementById('devices-list');
                const globalPanel = document.getElementById('swarm-global-panel');
                if (!container || (globalPanel && globalPanel.style.display !== 'none')) return;
                const tiles = Array.from(container.querySelectorAll('.device-tile[id^="device-tile-"]'));
                const hasSameDevices = tiles.length === currentDevices.length
                    && currentDevices.every(device => document.getElementById(`device-tile-${cssSafeId(device.device_id)}`));
                if (!hasSameDevices) {
                    displayDevices();
                    return;
                }
                currentDevices.forEach(device => {
                    const tile = document.getElementById(`device-tile-${cssSafeId(device.device_id)}`);
                    if (!tile) return;
                    tile.classList.toggle('active', device.device_id === selectedDeviceId);
                    const name = tile.querySelector('[data-device-field="name"]');
                    const batocera = tile.querySelector('[data-device-field="batocera"]');
                    const counts = tile.querySelector('[data-device-field="counts"]');
                    const lastSeen = tile.querySelector('[data-device-field="last-seen"]');
                    if (name) name.textContent = device.device_name;
                    if (batocera) batocera.textContent = `Batocera: ${(device.system_info || {}).batocera_version || 'n/a'}`;
                    if (counts) counts.textContent = formatDeviceInventorySummary(device);
                    if (lastSeen) lastSeen.textContent = device.last_seen ? `Last seen: ${new Date(device.last_seen).toLocaleString()}` : 'Last seen unavailable';
                    updateDeviceTileBadge(tile, 'online', Boolean(device.online), 'Online', 'Offline', 'text-bg-danger');
                    updateDeviceTileBadge(tile, 'connected', Boolean(device.swarm_connected), 'Connected to Swarm', 'Not Connected to Swarm', 'text-bg-secondary');
                    updateDeviceTileBadge(tile, 'resolvable', Boolean(device.public_reachability && device.public_reachability.resolvable), 'Resolvable', 'Not Resolvable', 'text-bg-secondary');
                    updateDeviceTileBadge(tile, 'edge', Boolean(device.edge_online), 'Online via Edge', 'Not on Edge', 'text-bg-secondary');
                });
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
                currentDeviceView = 'overview';
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
                    [...(target.active || []), ...(target.queued || []), ...(target.recent || [])].forEach(row => {
                        rows.push({
                            ...row,
                            target_drone_id: row.target_drone_id || target.target_drone_id,
                            target_device_name: target.device_name || target.target_drone_id,
                        });
                    });
                });
                return rows;
            }

            function downloadRowKey(row) {
                return `${row.target_drone_id || ''}:${row.job_id || row.id || ''}`;
            }

            function downloadStatusClass(status) {
                return status === 'failed' ? 'danger'
                    : status === 'completed' ? 'success'
                    : status === 'cancelled' ? 'secondary'
                    : status === 'downloading' ? 'info'
                    : status === 'paused' ? 'warning'
                    : status === 'pending' ? 'dark'
                    : 'primary';
            }

            function renderDownloadActionButtons(row) {
                if (!canMutateSwarm()) return '';
                const status = String(row.status || '');
                const jobId = row.job_id || row.id;
                if (!jobId) return ''; // a not-yet-claimed pending placeholder has no job_id yet
                const target = escapeHtml(row.target_drone_id);
                const job = escapeHtml(jobId);
                const buttons = [];
                if (['queued', 'downloading', 'pending', 'paused'].includes(status)) {
                    buttons.push(`<button class="btn btn-outline-danger btn-sm" title="Cancel" onclick="cancelSwarmDownload('${target}','${job}')"><i class="bi bi-x-circle"></i></button>`);
                }
                if (['queued', 'pending', 'downloading'].includes(status)) {
                    buttons.push(`<button class="btn btn-outline-warning btn-sm" title="Pause" onclick="pauseSwarmDownload('${target}','${job}')"><i class="bi bi-pause-fill"></i></button>`);
                }
                if (status === 'paused') {
                    buttons.push(`<button class="btn btn-outline-success btn-sm" title="Resume" onclick="resumeSwarmDownload('${target}','${job}')"><i class="bi bi-play-fill"></i></button>`);
                }
                return buttons.join(' ');
            }

            function renderSwarmDownloadsTable(rows) {
                return `<div class="table-responsive"><table class="table table-sm align-middle bff-stack">
                    <thead><tr>
                        <th>Target</th><th>Source</th><th>Status</th><th>File</th><th>Size</th><th>Downloaded</th><th>Progress</th><th>Speed</th><th></th>
                    </tr></thead>
                    <tbody>${rows.map(row => {
                        const pct = Number(row.percentage || 0);
                        const fileLabel = [row.file_path || row.relative_path || row.rom_path || row.rom_name || '', row.artwork_type || ''].filter(Boolean).join(' / ');
                        return `<tr id="swarm-download-${cssSafeId(downloadRowKey(row))}">
                            <td class="small"><div class="fw-semibold">${escapeHtml(row.target_device_name || row.target_drone_id || 'n/a')}</div><div class="small text-muted mono">${escapeHtml(row.target_drone_id || '')}</div></td>
                            <td class="small mono">${escapeHtml(row.source_drone_id || 'n/a')}</td>
                            <td><span class="badge text-bg-${downloadStatusClass(row.status)}" data-download-field="status">${escapeHtml(row.status || 'queued')}</span></td>
                            <td class="small">${escapeHtml(fileLabel)}${row.asset_type ? `<div class="small text-muted">${escapeHtml(row.asset_type)}</div>` : ''}${row.failure_reason || row.error_message ? `<div class="text-danger" data-download-field="reason">${escapeHtml(row.failure_reason || row.error_message)}</div>` : ''}</td>
                            <td class="small">${formatBytes(row.total_bytes || row.file_size)}</td>
                            <td class="small" data-download-field="downloaded">${formatBytes(row.downloaded_bytes || row.bytes_transferred)}</td>
                            <td style="min-width:150px"><div class="progress" style="height:.55rem"><div class="progress-bar" data-download-field="progress-bar" style="width:${Math.max(0, Math.min(100, pct))}%"></div></div><div class="small text-muted" data-download-field="progress-text">${pct.toFixed(1)}%</div></td>
                            <td class="small" data-download-field="speed">${row.transfer_speed_bps ? `${formatBytes(row.transfer_speed_bps)}/s` : ''}</td>
                            <td data-download-field="actions">${renderDownloadActionButtons(row)}</td>
                        </tr>`;
                    }).join('')}</tbody></table></div>`;
            }

            function updateSwarmDownloadsInPlace(rows) {
                const existingRows = Array.from(document.querySelectorAll('[id^="swarm-download-"]'));
                const hasSameRows = existingRows.length === rows.length
                    && rows.every(row => document.getElementById(`swarm-download-${cssSafeId(downloadRowKey(row))}`));
                if (!hasSameRows) return false;
                rows.forEach(row => {
                    const tableRow = document.getElementById(`swarm-download-${cssSafeId(downloadRowKey(row))}`);
                    const pct = Math.max(0, Math.min(100, Number(row.percentage || 0)));
                    const status = tableRow.querySelector('[data-download-field="status"]');
                    const downloaded = tableRow.querySelector('[data-download-field="downloaded"]');
                    const progressBar = tableRow.querySelector('[data-download-field="progress-bar"]');
                    const progressText = tableRow.querySelector('[data-download-field="progress-text"]');
                    const speed = tableRow.querySelector('[data-download-field="speed"]');
                    const actions = tableRow.querySelector('[data-download-field="actions"]');
                    if (status) {
                        status.className = `badge text-bg-${downloadStatusClass(row.status)}`;
                        status.textContent = row.status || 'queued';
                    }
                    if (downloaded) downloaded.textContent = formatBytes(row.downloaded_bytes || row.bytes_transferred);
                    if (progressBar) progressBar.style.width = `${pct}%`;
                    if (progressText) progressText.textContent = `${pct.toFixed(1)}%`;
                    if (speed) speed.textContent = row.transfer_speed_bps ? `${formatBytes(row.transfer_speed_bps)}/s` : '';
                    if (actions) {
                        const nextActions = renderDownloadActionButtons(row);
                        if (actions.innerHTML !== nextActions) actions.innerHTML = nextActions;
                    }
                });
                return true;
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
                    const response = await apiGet('/api/downloads', { showLoader: !options.quiet });
                    if (!response.ok) throw new Error('Failed to load downloads');
                    const payload = await response.json();
                    const targets = payload.targets || [];
                    const rows = flattenDownloadTargets(targets);
                    if (!rows.length) {
                        if (panel.dataset.downloadState !== 'empty') panel.innerHTML = '<div class="empty-state">No downloads in flight.</div>';
                        panel.dataset.downloadState = 'empty';
                        startSwarmDownloadsAutoRefresh();
                        return;
                    }
                    if (options.quiet && updateSwarmDownloadsInPlace(rows)) {
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
                    panel.dataset.downloadState = 'rows';
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

            async function pauseSwarmDownload(deviceId, jobId) {
                if (!deviceId || !jobId) return;
                const response = await fetch(`/api/devices/${encodeURIComponent(deviceId)}/downloads/${encodeURIComponent(jobId)}/pause`, {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (!response.ok) {
                    showMessage('Unable to queue pause request.', 'error');
                    return;
                }
                showMessage('Pause request sent to the target Drone.', 'success');
                await showSwarmDownloads(false);
            }

            async function resumeSwarmDownload(deviceId, jobId) {
                if (!deviceId || !jobId) return;
                const response = await fetch(`/api/devices/${encodeURIComponent(deviceId)}/downloads/${encodeURIComponent(jobId)}/resume`, {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (!response.ok) {
                    showMessage('Unable to queue resume request.', 'error');
                    return;
                }
                showMessage('Resume request sent to the target Drone.', 'success');
                await showSwarmDownloads(false);
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
                params.set('per_page', '100');
                params.set('page', String(swarmMasterPage));
                panel.innerHTML = `<div class="card"><div class="card-body py-2">Loading swarm master list...</div></div>`;
                try {
                    if (!currentDevices.length) await loadDevices({applyRoute: false});
                    const response = await apiGet('/api/master-roms' + (params.toString() ? `?${params.toString()}` : ''));
                    if (!response.ok) throw new Error('Failed to load master list');
                    const payload = await response.json();
                    const rows = payload.roms || [];
                    const total = payload.total || rows.length;
                    const page = payload.page || swarmMasterPage;
                    const perPage = payload.per_page || 100;
                    const pageCount = Math.max(1, Math.ceil(total / perPage));
                    swarmMasterPage = page;
                    const systemsResp = await apiGet('/api/systems');
                    const systemsPayload = systemsResp.ok ? await systemsResp.json() : {systems: []};
                    const systemOptions = (systemsPayload.systems || [])
                        .map(row => row.system_name)
                        .filter(Boolean)
                        .sort((a, b) => a.localeCompare(b));
                    const bulkDevices = currentDevices
                        .filter(device => device && device.device_id)
                        .sort((a, b) => String(a.device_name || a.device_id).localeCompare(String(b.device_name || b.device_id)));
                    const renderPageBtn = (p) =>
                        `<button class="btn btn-sm ${p === page ? 'btn-primary' : 'btn-outline-secondary'}" onclick="setSwarmMasterPage(${p})">${p}</button>`;
                    const pageBtns = [];
                    if (pageCount <= 7) {
                        for (let i = 1; i <= pageCount; i++) pageBtns.push(renderPageBtn(i));
                    } else {
                        const start = Math.max(1, page - 2);
                        const end = Math.min(pageCount, page + 2);
                        if (start > 1) pageBtns.push(renderPageBtn(1));
                        if (start > 2) pageBtns.push('<span class="px-1">&hellip;</span>');
                        for (let i = start; i <= end; i++) pageBtns.push(renderPageBtn(i));
                        if (end < pageCount - 1) pageBtns.push('<span class="px-1">&hellip;</span>');
                        if (end < pageCount) pageBtns.push(renderPageBtn(pageCount));
                    }
                    const paginationHtml = pageCount > 1 ? `
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                            <div class="small text-muted">${total} unique ROMs &middot; Page ${page} of ${pageCount}</div>
                            <div class="d-flex flex-wrap align-items-center gap-1">
                                <button class="btn btn-sm btn-outline-secondary" ${page <= 1 ? 'disabled' : ''} onclick="setSwarmMasterPage(${Math.max(1, page - 1)})">Previous</button>
                                ${pageBtns.join('')}
                                <button class="btn btn-sm btn-outline-secondary" ${page >= pageCount ? 'disabled' : ''} onclick="setSwarmMasterPage(${Math.min(pageCount, page + 1)})">Next</button>
                            </div>
                        </div>` : `<div class="small text-muted mb-2">${total} unique ROMs across approved Drones</div>`;
                    panel.innerHTML = `<div class="card"><div class="card-body py-2">
                        <div class="d-flex flex-wrap align-items-end gap-2 mb-3">
                            <div style="flex:1;min-width:240px">
                                <label class="form-label" for="swarm-master-search">Search Master List</label>
                                <input id="swarm-master-search" class="form-control" type="search" value="${escapeHtml(q)}" placeholder="System, ROM, fingerprint, Drone">
                            </div>
                            <button class="btn btn-primary" onclick="submitSwarmMasterSearch()">Search</button>
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
                                    ${renderArtworkCheckboxDropdown({
                                        id: 'bulk-sync-drones',
                                        label: 'Drones',
                                        allLabel: 'Select Drones',
                                        allValue: 'none',
                                        items: bulkDevices.map(device => ({value: device.device_id, label: device.device_name || device.device_id})),
                                        selectedValues: [],
                                        inputClass: 'bulk-sync-drone',
                                        onChange: 'handleBulkSyncDropdownChange',
                                    })}
                                </div>
                                <div class="col-12 col-lg-6">
                                    ${renderArtworkCheckboxDropdown({
                                        id: 'bulk-sync-systems',
                                        label: 'Systems',
                                        allLabel: 'Select systems',
                                        allValue: 'none',
                                        items: systemOptions,
                                        selectedValues: [],
                                        inputClass: 'bulk-sync-system',
                                        onChange: 'handleBulkSyncDropdownChange',
                                    })}
                                </div>
                            </div>
                        </div>
                        ${paginationHtml}
                        <div class="table-responsive"><table class="table table-sm align-middle bff-stack"><thead><tr>
                            <th>System</th><th>ROM</th><th>Size</th><th>Drones</th>
                        </tr></thead><tbody>
                            ${rows.map(row => {
                                const devices = (row.devices || []).map(d => d.device_name || d.device_id).join(', ');
                                const filenames = (row.filenames || []).length > 1 ? `<div class="small text-muted">${(row.filenames || []).map(escapeHtml).join('<br>')}</div>` : '';
                                const sizeText = row.file_size ? `${(Number(row.file_size) / 1024 / 1024).toFixed(2)} MB` : '';
                                return `<tr>
                                    <td>${escapeHtml(row.system_name || '')}</td>
                                    <td>${escapeHtml(row.rom_name || row.file_path || '')}${filenames}</td>
                                    <td class="small text-muted">${escapeHtml(sizeText)}</td>
                                    <td class="small">${escapeHtml(devices)}</td>
                                </tr>`;
                            }).join('')}
                        </tbody></table></div>
                        ${rows.length ? '' : '<div class="small text-muted">No ROMs matched.</div>'}
                    </div></div>`;
                    document.getElementById('swarm-master-search')?.addEventListener('keydown', event => {
                        if (event.key === 'Enter') submitSwarmMasterSearch();
                    });
                } catch (error) {
                    panel.innerHTML = '<div class="empty-state">Unable to load swarm master list.</div>';
                }
            }

            async function showSwarmGameplay(updateUrl = true) {
                if (updateUrl) {
                    setSwarmView('gameplay');
                    return;
                }
                setActiveSwarmView('gameplay');
                const panel = document.getElementById('swarm-global-panel');
                const list = document.getElementById('devices-list');
                if (!panel) return;
                if (list) list.style.display = 'none';
                panel.style.display = 'block';
                panel.innerHTML = `<div class="card"><div class="card-body py-2">Loading play history...</div></div>`;
                try {
                    const response = await apiGet('/api/gameplay?limit=200');
                    if (!response.ok) throw new Error('Failed to load play history');
                    const payload = await response.json();
                    const rows = payload.gamelogs || [];
                    if (!rows.length) {
                        panel.innerHTML = `<div class="card"><div class="card-body py-2">
                            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
                                <strong><i class="bi bi-controller me-1"></i>Play History</strong>
                                <button class="btn btn-sm btn-outline-secondary" onclick="showSwarmGameplay(false)"><i class="bi bi-arrow-repeat me-1"></i>Refresh</button>
                            </div>
                            <div class="empty-state">No gameplay sessions reported across the swarm yet.</div>
                        </div></div>`;
                        return;
                    }
                    const body = rows.map(log => {
                        const when = log.played_at ? new Date(log.played_at).toLocaleString() : '—';
                        const system = log.system_name || '';
                        const game = log.game_name || log.rom_name || log.rom_path || 'Unknown';
                        return `<tr>
                            <td class="small">${escapeHtml(log.device_name || log.device_id || '—')}</td>
                            <td>${system ? `<span class="badge text-bg-secondary">${escapeHtml(system)}</span>` : '<span class="text-muted">—</span>'}</td>
                            <td>${escapeHtml(game)}</td>
                            <td class="small text-nowrap">${escapeHtml(when)}</td>
                            <td class="small text-nowrap">${escapeHtml(formatGameplayDuration(log.duration_seconds))}</td>
                        </tr>`;
                    }).join('');
                    panel.innerHTML = `<div class="card"><div class="card-body py-2">
                        <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
                            <strong><i class="bi bi-controller me-1"></i>Play History</strong>
                            <div class="d-flex align-items-center gap-2">
                                <span class="small text-muted">${rows.length} session${rows.length === 1 ? '' : 's'} across the swarm</span>
                                <button class="btn btn-sm btn-outline-secondary" onclick="showSwarmGameplay(false)"><i class="bi bi-arrow-repeat me-1"></i>Refresh</button>
                            </div>
                        </div>
                        <div class="table-responsive">
                            <table class="table table-sm table-hover align-middle gameplay-history-table bff-stack">
                                <thead><tr><th>Drone</th><th>System</th><th>Game</th><th>Played</th><th>Duration</th></tr></thead>
                                <tbody>${body}</tbody>
                            </table>
                        </div>
                    </div></div>`;
                } catch (error) {
                    panel.innerHTML = '<div class="empty-state">Unable to load play history.</div>';
                }
            }

            function setSwarmMasterPage(page) {
                swarmMasterPage = Math.max(1, page);
                updateRouteQuery({ mp: swarmMasterPage });
                showSwarmMasterList(false);
                scrollAppToTop();
            }

            function submitSwarmMasterSearch() {
                swarmMasterPage = 1;
                updateRouteQuery({ mp: 1 });
                showSwarmMasterList(false);
                scrollAppToTop();
            }

            async function queueBulkSwarmSync() {
                const deviceIds = Array.from(document.querySelectorAll('.bulk-sync-drone:checked')).map(input => input.value).filter(value => value !== 'none');
                const systems = Array.from(document.querySelectorAll('.bulk-sync-system:checked')).map(input => input.value).filter(value => value !== 'none');
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

            function handleBulkSyncDropdownChange(event) {
                const input = event.target;
                if (!input || input.value !== 'none') return;
                const menu = input.closest('.app-checkbox-menu');
                menu?.querySelectorAll('input[type="checkbox"]').forEach(option => {
                    option.checked = false;
                });
            }

            function selectDevice(deviceId) {
                selectedDeviceId = deviceId;
                currentDeviceView = 'overview';
                currentDeviceSystems = {};
                currentDeviceSystemsPage = { total: 0, page: 1, per_page: SYSTEMS_FETCH_PAGE_SIZE };
                currentSystemRomPages = {};
                currentSystemBiosPages = {};
                currentBiosFilePage = { bios: [], total: 0, nextOffset: 0, loading: false, error: false };
                currentBiosSummary = { total: 0, loading: false, error: false };
                selectedSystemName = null;
                selectedFileCategory = null;
                deviceRomSearchQuery = '';
                deviceAssetScope = 'mine';
                deviceSystemsPage = 1;
                setRoute('devices', deviceId, 'overview');
            }

            function formatGameplayDuration(seconds) {
                const total = Number(seconds);
                if (!Number.isFinite(total) || total <= 0) return '—';
                if (total < 60) return `${Math.round(total)}s`;
                const minutes = Math.floor(total / 60);
                const hours = Math.floor(minutes / 60);
                if (hours > 0) return `${hours}h ${minutes % 60}m`;
                return `${minutes}m`;
            }

            async function loadDeviceSystemsView() {
                const scopeSelect = document.getElementById('device-asset-scope-filter');
                if (scopeSelect) scopeSelect.value = deviceAssetScope;
                await loadDeviceSystems();
            }

            // 'status' param sent to the presence-aware master-roms/master-bios
            // endpoints: 'present' for the default "My Files" scope (only what's
            // already on this drone), unset for "All" (fleet-wide, both present and
            // missing), 'missing' for "Missing" (fleet-wide, not on this drone).
            function deviceAssetStatusParam() {
                if (deviceAssetScope === 'missing') return 'missing';
                if (deviceAssetScope === 'all') return '';
                return 'present';
            }

            function handleDeviceAssetScopeChange(value) {
                const next = ['mine', 'all', 'missing'].includes(value) ? value : 'mine';
                if (next === deviceAssetScope) return;
                deviceAssetScope = next;
                deviceSystemsPage = 1;
                selectedSystemName = null;
                selectedFileCategory = null;
                currentSystemRomPages = {};
                currentSystemBiosPages = {};
                updateRouteQuery({ sc: deviceAssetScope === 'mine' ? null : deviceAssetScope, dsp: null, syn: null, fc: null });
                loadDeviceSystemsView();
            }

            async function loadDeviceSystems() {
                if (!selectedDeviceId) {
                    return;
                }
                try {
                    const q = (deviceRomSearchQuery || '').trim();
                    let total = 0;
                    let systems = [];
                    if (deviceAssetScope === 'mine') {
                        let page = 1;
                        const rows = [];
                        do {
                            const params = new URLSearchParams();
                            if (q) params.set('q', q);
                            params.set('page', String(page));
                            params.set('per_page', String(SYSTEMS_FETCH_PAGE_SIZE));
                            const response = await apiGet(`/api/devices/${selectedDeviceId}/systems?${params.toString()}`);
                            if (!response.ok) throw new Error('Failed to load device systems');
                            const data = await response.json();
                            const page_rows = data.systems || [];
                            total = Number(data.total || page_rows.length || 0);
                            rows.push(...page_rows);
                            if (!page_rows.length || rows.length >= total) break;
                            page += 1;
                        } while (page < 100);
                        systems = rows;
                    } else {
                        // Fleet-wide, unpaginated: lets the tree show systems this
                        // drone has zero files for yet, so "All"/"Missing" can
                        // surface games worth pulling in from other Drones. The
                        // search matches system name OR any game title within it
                        // server-side, so it's passed through rather than
                        // re-filtered here by system name only.
                        const systemsParams = new URLSearchParams();
                        if (q) systemsParams.set('q', q);
                        const response = await apiGet(`/api/systems${systemsParams.toString() ? `?${systemsParams.toString()}` : ''}`);
                        if (!response.ok) throw new Error('Failed to load systems');
                        const data = await response.json();
                        systems = data.systems || [];
                        total = systems.length;
                    }
                    await loadBiosSummary();
                    currentSystemRomPages = {};
                    currentSystemBiosPages = {};
                    currentBiosFilePage = { bios: [], total: currentBiosSummary.total || 0, nextOffset: 0, loading: false, error: false };
                    deviceSystemsPage = 1;
                    currentDeviceSystemsPage = {
                        total,
                        page: 1,
                        per_page: SYSTEMS_FETCH_PAGE_SIZE,
                    };
                    currentDeviceSystems = systems.reduce((acc, row) => {
                        const name = row.system_name || row.name || '';
                        if (name) acc[name] = row;
                        return acc;
                    }, {});
                    if (selectedSystemName && selectedSystemName !== BIOS_TREE_ROOT && !currentDeviceSystems[selectedSystemName]) {
                        selectedSystemName = null;
                        selectedFileCategory = null;
                        updateRouteQuery({ syn: null, fc: null });
                    }
                    if (selectedSystemName === BIOS_TREE_ROOT && currentBiosSummary.total <= 0) {
                        selectedSystemName = null;
                        selectedFileCategory = null;
                        updateRouteQuery({ syn: null, fc: null });
                    }
                    displaySystemsTree();
                    if (selectedSystemName === BIOS_TREE_ROOT) {
                        await loadBiosFilePage({ reset: true });
                    } else if (selectedSystemName && currentDeviceSystems[selectedSystemName] && selectedFileCategory === 'bios') {
                        await loadSystemBiosFilePage(selectedSystemName, { reset: true });
                    } else if (selectedSystemName && currentDeviceSystems[selectedSystemName]) {
                        await loadSystemRomPage(selectedSystemName, { reset: true });
                    }
                } catch (error) {
                    console.error('Error loading systems:', error);
                    const container = document.getElementById('systems-list');
                    if (container) container.innerHTML = '<div class="empty-state">Unable to load systems.</div>';
                }
            }

            function submitDeviceRomSearch() {
                const input = document.getElementById('device-rom-search');
                deviceRomSearchQuery = (input ? input.value : '').trim();
                deviceSystemsPage = 1;
                selectedSystemName = null;
                selectedFileCategory = null;
                currentSystemRomPages = {};
                currentSystemBiosPages = {};
                currentBiosFilePage = { bios: [], total: 0, nextOffset: 0, loading: false, error: false };
                updateRouteQuery({ rq: deviceRomSearchQuery, dsp: null, syn: null, fc: null });
                loadDeviceSystemsView();
                scrollAppToTop();
            }

            function handleDeviceRomSearchKeydown(event) {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                submitDeviceRomSearch();
            }

            function systemRomState(systemName) {
                const key = String(systemName || '');
                if (!currentSystemRomPages[key]) {
                    currentSystemRomPages[key] = { roms: [], total: 0, nextOffset: 0, loading: false, error: false };
                }
                return currentSystemRomPages[key];
            }

            function selectSystem(systemName, category = 'games') {
                if (!systemName) return;
                if (selectedSystemName === systemName) {
                    selectedSystemName = null;
                    selectedFileCategory = null;
                    updateRouteQuery({ syn: null, fc: null });
                    displaySystemsTree();
                    return;
                }
                selectedSystemName = systemName;
                selectedFileCategory = category;
                updateRouteQuery({ syn: systemName, fc: selectedFileCategory });
                displaySystemsTree();
                if (category === 'bios') {
                    loadSystemBiosFilePage(systemName, { reset: true });
                } else {
                    currentSystemRomPages[systemName] = { roms: [], total: 0, nextOffset: 0, loading: true, error: false };
                    loadSystemRomPage(systemName, { reset: true });
                }
            }

            function selectBiosRoot() {
                if (selectedSystemName === BIOS_TREE_ROOT) {
                    selectedSystemName = null;
                    selectedFileCategory = null;
                    updateRouteQuery({ syn: null, fc: null });
                    displaySystemsTree();
                    return;
                }
                selectedSystemName = BIOS_TREE_ROOT;
                selectedFileCategory = 'bios';
                currentBiosFilePage = { bios: [], total: currentBiosSummary.total || 0, nextOffset: 0, loading: true, error: false };
                updateRouteQuery({ syn: BIOS_TREE_ROOT, fc: selectedFileCategory });
                displaySystemsTree();
                loadBiosFilePage({ reset: true });
            }

            function selectFileCategory(rootName, category) {
                if (rootName === BIOS_TREE_ROOT) {
                    if (selectedSystemName !== BIOS_TREE_ROOT) {
                        selectBiosRoot();
                        return;
                    }
                    selectedFileCategory = 'bios';
                    updateRouteQuery({ syn: BIOS_TREE_ROOT, fc: selectedFileCategory });
                    displaySystemsTree();
                    return;
                }
                if (selectedSystemName !== rootName) {
                    selectSystem(rootName, category);
                    return;
                }
                if (selectedFileCategory === category) {
                    return;
                }
                selectedFileCategory = category;
                updateRouteQuery({ syn: rootName, fc: selectedFileCategory });
                displaySystemsTree();
                if (category === 'bios' && !Object.prototype.hasOwnProperty.call(currentSystemBiosPages, rootName)) {
                    loadSystemBiosFilePage(rootName, { reset: true });
                } else if (category === 'games' && !Object.prototype.hasOwnProperty.call(currentSystemRomPages, rootName)) {
                    currentSystemRomPages[rootName] = { roms: [], total: 0, nextOffset: 0, loading: true, error: false };
                    loadSystemRomPage(rootName, { reset: true });
                }
            }

            function loadMoreSystemRoms(systemName = selectedSystemName) {
                if (!systemName || systemName === BIOS_TREE_ROOT) return;
                loadSystemRomPage(systemName, { reset: false });
            }

            function loadMoreBiosFiles() {
                loadBiosFilePage({ reset: false });
            }

            function loadMoreSystemBiosFiles(systemName = selectedSystemName) {
                if (!systemName || systemName === BIOS_TREE_ROOT) return;
                loadSystemBiosFilePage(systemName, { reset: false });
            }

            async function refreshSystemsAndRoms(triggerEl) {
                // Reload only the visible systems page; individual ROM pages are fetched
                // when a system row is expanded.
                if (!selectedDeviceId) return;
                const btn = triggerEl || (typeof event !== 'undefined' ? event.target : null);
                const original = btn ? btn.innerHTML : null;
                if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Refreshing'; }
                try {
                    await loadDeviceSystemsView();
                    showMessage('Systems & ROMs refreshed from Overmind.', 'success');
                } catch (err) {
                    console.error('Error refreshing systems:', err);
                    showMessage('Failed to refresh systems.', 'danger');
                } finally {
                    if (btn) { btn.disabled = false; btn.innerHTML = original; }
                }
            }

            async function rescanDroneMetadata() {
                // Force the Drone to re-scan its library and re-upload (roms + bios + fingerprint).
                // Uses collect_rom_metadata so the Drone reuses cached fingerprint where possible.
                if (!selectedDeviceId) return;
                try {
                    await queueDeviceAction('collect_rom_metadata', { confirm: true, notify: false });
                    showMessage('Drone rescan queued — systems, ROMs, BIOS and fingerprint will resync shortly.', 'success');
                } catch (err) {
                    // queueDeviceAction already surfaced the error.
                }
            }

            async function syncMissingSystemFiles(systemName) {
                if (!systemName || !selectedDeviceId) return;
                if (!confirm(`Queue sync for all missing games in ${systemName} on this Drone?`)) return;
                try {
                    await syncSystem(systemName);
                    await loadSystemRomPage(systemName, { reset: true });
                } catch (err) {
                    console.error('Error syncing system:', err);
                    showMessage(err.message || 'Failed to queue system sync.', 'error');
                }
            }

            function filteredSystemEntries() {
                return Object.entries(currentDeviceSystems).reduce((entries, [systemName, summary]) => {
                    entries.push([systemName, summary]);
                    return entries;
                }, []);
            }

            async function loadSystemRomPage(systemName, options = {}) {
                if (!selectedDeviceId || !systemName) return;
                const reset = options.reset === true;
                const state = reset
                    ? { roms: [], total: Number((currentDeviceSystems[systemName] || {}).rom_count || 0), nextOffset: 0, loading: false, error: false }
                    : systemRomState(systemName);
                if (!reset && state.loading) return;
                const requestDeviceId = selectedDeviceId;
                const requestGen = (systemRomRequestSeq[systemName] = (systemRomRequestSeq[systemName] || 0) + 1);
                const existingRows = reset ? [] : (state.roms || []);
                const offset = reset ? 0 : Number(state.nextOffset ?? existingRows.length);
                const pageSize = TREE_FILE_LOAD_SIZE;
                currentSystemRomPages[systemName] = {
                    ...state,
                    roms: existingRows,
                    loading: true,
                    error: false,
                };
                renderSystemRomPage(systemName);
                try {
                    const q = (deviceRomSearchQuery || '').trim();
                    let url;
                    if (deviceAssetScope === 'mine') {
                        const romParams = new URLSearchParams();
                        romParams.set('system_name', systemName);
                        romParams.set('offset', String(offset));
                        romParams.set('per_page', String(pageSize));
                        if (q) romParams.set('q', q);
                        url = `/api/devices/${selectedDeviceId}/roms?${romParams.toString()}`;
                    } else {
                        // "All"/"Missing": presence-aware, fleet-wide list (same source
                        // the BIOS tree leaves already use) so Sync buttons + badges
                        // can appear here too.
                        const romParams = new URLSearchParams();
                        romParams.set('system', systemName);
                        romParams.set('page', String(Math.floor(offset / pageSize) + 1));
                        romParams.set('per_page', String(pageSize));
                        if (q) romParams.set('q', q);
                        const status = deviceAssetStatusParam();
                        if (status) romParams.set('status', status);
                        url = `/api/devices/${selectedDeviceId}/master-roms?${romParams.toString()}`;
                    }
                    const romResponse = await apiGet(url);
                    if (!romResponse.ok) throw new Error('Failed to load system ROMs');
                    const payload = await romResponse.json();
                    if (selectedDeviceId !== requestDeviceId || systemRomRequestSeq[systemName] !== requestGen) return;
                    const rows = payload.roms || [];
                    const loadedRows = reset ? rows : [...(currentSystemRomPages[systemName]?.roms || []), ...rows];
                    const summary = currentDeviceSystems[systemName] || {};
                    currentSystemRomPages[systemName] = {
                        roms: loadedRows,
                        total: Number(payload.total ?? summary.rom_count ?? loadedRows.length),
                        nextOffset: offset + rows.length,
                        per_page: Number(payload.per_page || pageSize),
                        loading: false,
                        error: false,
                    };
                } catch (error) {
                    console.error('Error loading system ROMs:', error);
                    if (systemRomRequestSeq[systemName] !== requestGen) return;
                    const summary = currentDeviceSystems[systemName] || {};
                    currentSystemRomPages[systemName] = {
                        ...state,
                        roms: existingRows,
                        total: Number(state.total || summary.rom_count || 0),
                        nextOffset: offset,
                        loading: false,
                        error: true,
                    };
                }
                if (selectedSystemName === systemName) renderSystemRomPage(systemName);
            }

            async function loadBiosSummary() {
                if (!selectedDeviceId) return;
                currentBiosSummary = { ...currentBiosSummary, loading: true, error: false };
                try {
                    const params = new URLSearchParams();
                    const q = (deviceRomSearchQuery || '').trim();
                    if (q) params.set('q', q);
                    params.set('page', '1');
                    params.set('per_page', '1');
                    params.set('unassigned', 'true');
                    const status = deviceAssetStatusParam();
                    if (status) params.set('status', status);
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/master-bios?${params.toString()}`);
                    if (!response.ok) throw new Error('Failed to load BIOS summary');
                    const payload = await response.json();
                    currentBiosSummary = { total: Number(payload.total || 0), loading: false, error: false };
                } catch (error) {
                    console.error('Error loading BIOS summary:', error);
                    currentBiosSummary = { total: 0, loading: false, error: true };
                }
            }

            async function loadBiosFilePage(options = {}) {
                if (!selectedDeviceId) return;
                const reset = options.reset === true;
                const state = reset
                    ? { bios: [], total: Number(currentBiosSummary.total || 0), nextOffset: 0, loading: false, error: false }
                    : currentBiosFilePage;
                if (!reset && state.loading) return;
                const requestDeviceId = selectedDeviceId;
                const existingRows = reset ? [] : (state.bios || []);
                const offset = reset ? 0 : Number(state.nextOffset ?? existingRows.length);
                const page = Math.floor(offset / TREE_FILE_LOAD_SIZE) + 1;
                currentBiosFilePage = {
                    ...state,
                    bios: existingRows,
                    loading: true,
                    error: false,
                };
                renderBiosFilePage();
                try {
                    const params = new URLSearchParams();
                    const q = (deviceRomSearchQuery || '').trim();
                    if (q) params.set('q', q);
                    params.set('page', String(page));
                    params.set('per_page', String(TREE_FILE_LOAD_SIZE));
                    params.set('unassigned', 'true');
                    const status = deviceAssetStatusParam();
                    if (status) params.set('status', status);
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/master-bios?${params.toString()}`);
                    if (!response.ok) throw new Error('Failed to load BIOS files');
                    const payload = await response.json();
                    if (selectedDeviceId !== requestDeviceId || selectedSystemName !== BIOS_TREE_ROOT) return;
                    const rows = payload.bios || [];
                    const loadedRows = reset ? rows : [...(currentBiosFilePage.bios || []), ...rows];
                    currentBiosFilePage = {
                        bios: loadedRows,
                        total: Number(payload.total ?? currentBiosSummary.total ?? loadedRows.length),
                        nextOffset: offset + rows.length,
                        per_page: Number(payload.per_page || TREE_FILE_LOAD_SIZE),
                        loading: false,
                        error: false,
                    };
                    currentBiosSummary = { total: currentBiosFilePage.total, loading: false, error: false };
                } catch (error) {
                    console.error('Error loading BIOS files:', error);
                    currentBiosFilePage = {
                        ...state,
                        bios: existingRows,
                        total: Number(state.total || currentBiosSummary.total || 0),
                        nextOffset: offset,
                        loading: false,
                        error: true,
                    };
                }
                renderBiosFilePage();
            }

            function renderSystemRomPage(systemName) {
                const target = document.getElementById(`tree-files-${cssSafeId(systemName)}`);
                if (!target) return;
                const payload = currentSystemRomPages[systemName];
                const summary = currentDeviceSystems[systemName] || {};
                if (!payload) {
                    target.innerHTML = '<div class="small text-muted tree-grid-empty">Select Games to load files.</div>';
                    return;
                }
                const roms = payload.roms || [];
                const total = Number(payload.total || summary.rom_count || roms.length);
                const loaded = roms.length;
                const hasMore = loaded < total;
                const firstLoad = payload.loading && !loaded;
                const showPresence = deviceAssetScope !== 'mine';
                target.innerHTML = firstLoad
                    ? '<div class="tree-grid-empty small text-muted">Loading first 10 games...</div>'
                    : `
                        ${payload.error ? '<div class="alert alert-danger py-2 small mb-2">Unable to load games for this system.</div>' : ''}
                        <div class="tree-leaf-list">
                            ${roms.map(rom => {
                                const path = rom.file_path || rom.relative_path || rom.rom_name || '';
                                const label = rom.rom_name || path;
                                const present = !!rom.present_on_selected;
                                const sources = (rom.devices || []).map(d => d.device_name || d.device_id).join(', ');
                                const tooltipParts = [rom.rom_fingerprint ? `fingerprint: ${rom.rom_fingerprint}` : path];
                                if (sources) tooltipParts.push(sources);
                                const size = rom.file_size ? formatBytes(rom.file_size) : 'n/a';
                                const showSync = showPresence && !present && rom.devices && rom.devices.length;
                                const rowPayload = JSON.stringify(rom).replace(/'/g, '&#39;');
                                return `
                                    <div class="tree-grid-row tree-leaf-row">
                                        <div class="tree-grid-main">
                                            <i class="bi bi-file-earmark-binary tree-grid-icon"></i>
                                            <div class="tree-grid-label text-truncate" title="${escapeHtml(tooltipParts.join(' · '))}">
                                                <span class="fw-semibold">${escapeHtml(label)}</span>
                                            </div>
                                        </div>
                                        <div class="tree-grid-meta">${escapeHtml(size)}</div>
                                        ${showPresence ? `
                                            <div class="tree-grid-action">
                                                <span class="badge ${present ? 'text-bg-success' : (rom.devices && rom.devices.length ? 'text-bg-secondary' : 'text-bg-danger')}">${present ? 'Present' : (rom.devices && rom.devices.length ? 'Missing' : 'Unavailable')}</span>
                                                ${showSync ? `<button class="btn btn-primary btn-sm" title="Download" aria-label="Download" onclick='syncRom(${rowPayload})'><i class="bi bi-download"></i></button>` : ''}
                                            </div>
                                        ` : ''}
                                    </div>
                                `;
                            }).join('') || '<div class="tree-grid-empty small text-muted">No games reported for this system.</div>'}
                        </div>
                        <div class="tree-grid-more">
                            <span class="small text-muted">${total ? `Showing ${loaded.toLocaleString()} of ${total.toLocaleString()}` : 'No games reported'}</span>
                            <button class="btn btn-outline-primary btn-sm" type="button" ${!hasMore || payload.loading ? 'disabled' : ''} onclick="loadMoreSystemRoms(${jsAttr(systemName)})">
                                ${payload.loading && loaded ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>' : '<i class="bi bi-plus-circle me-1"></i>'}
                                Show more
                            </button>
                        </div>
                    `;
            }

            function renderBiosFilePage() {
                const target = document.getElementById(`tree-files-${cssSafeId(BIOS_TREE_ROOT)}`);
                if (!target) return;
                const payload = currentBiosFilePage;
                const rows = payload.bios || [];
                const total = Number(payload.total || currentBiosSummary.total || rows.length);
                const loaded = rows.length;
                const hasMore = loaded < total;
                const firstLoad = payload.loading && !loaded;
                target.innerHTML = firstLoad
                    ? '<div class="tree-grid-empty small text-muted">Loading first 10 BIOS files...</div>'
                    : `
                        ${payload.error ? '<div class="alert alert-danger py-2 small mb-2">Unable to load BIOS files.</div>' : ''}
                        <div class="tree-leaf-list">
                            ${rows.map(row => {
                                const present = !!row.present_on_selected;
                                const sources = (row.devices || []).map(d => d.device_name || d.device_id).join(', ');
                                const path = row.file_path || row.bios_name || row.relative_path || '';
                                const sizeText = row.file_size ? formatBytes(row.file_size) : 'n/a';
                                const showSync = !present && row.devices && row.devices.length;
                                const rowPayload = encodeURIComponent(JSON.stringify(Object.assign({}, row)));
                                const tooltipParts = [row.bios_md5 ? `md5: ${row.bios_md5}` : 'no MD5 reported'];
                                if (sources) tooltipParts.push(sources);
                                return `
                                    <div class="tree-grid-row tree-leaf-row">
                                        <div class="tree-grid-main">
                                            <i class="bi bi-cpu tree-grid-icon"></i>
                                            <div class="tree-grid-label text-truncate" title="${escapeHtml(tooltipParts.join(' · '))}">
                                                <span class="fw-semibold">${escapeHtml(path)}</span>
                                            </div>
                                        </div>
                                        <div class="tree-grid-meta">${escapeHtml(sizeText)}</div>
                                        <div class="tree-grid-action">
                                            <span class="badge ${present ? 'text-bg-success' : (row.devices && row.devices.length ? 'text-bg-secondary' : 'text-bg-danger')}">${present ? 'Present' : (row.devices && row.devices.length ? 'Missing' : 'Unavailable')}</span>
                                            ${showSync ? `<button class="btn btn-primary btn-sm" onclick="syncBiosEncoded('${rowPayload}')">Sync</button>` : ''}
                                        </div>
                                    </div>
                                `;
                            }).join('') || '<div class="tree-grid-empty small text-muted">No BIOS files found for this filter.</div>'}
                        </div>
                        <div class="tree-grid-more">
                            <span class="small text-muted">${total ? `Showing ${loaded.toLocaleString()} of ${total.toLocaleString()}` : 'No BIOS files reported'}</span>
                            <button class="btn btn-outline-primary btn-sm" type="button" ${!hasMore || payload.loading ? 'disabled' : ''} onclick="loadMoreBiosFiles()">
                                ${payload.loading && loaded ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>' : '<i class="bi bi-plus-circle me-1"></i>'}
                                Show more
                            </button>
                        </div>
                    `;
            }

            async function loadSystemBiosFilePage(systemName, options = {}) {
                if (!selectedDeviceId || !systemName) return;
                const reset = options.reset === true;
                const state = reset
                    ? { bios: [], total: 0, nextOffset: 0, loading: false, error: false }
                    : (currentSystemBiosPages[systemName] || { bios: [], total: 0, nextOffset: 0, loading: false, error: false });
                if (!reset && state.loading) return;
                const requestDeviceId = selectedDeviceId;
                const existingRows = reset ? [] : (state.bios || []);
                const offset = reset ? 0 : Number(state.nextOffset ?? existingRows.length);
                const page = Math.floor(offset / TREE_FILE_LOAD_SIZE) + 1;
                currentSystemBiosPages[systemName] = { ...state, bios: existingRows, loading: true, error: false };
                renderSystemBiosFilePage(systemName);
                try {
                    const params = new URLSearchParams();
                    const q = (deviceRomSearchQuery || '').trim();
                    if (q) params.set('q', q);
                    params.set('page', String(page));
                    params.set('per_page', String(TREE_FILE_LOAD_SIZE));
                    params.set('system_name', systemName);
                    const status = deviceAssetStatusParam();
                    if (status) params.set('status', status);
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/master-bios?${params.toString()}`);
                    if (!response.ok) throw new Error('Failed to load BIOS files');
                    const payload = await response.json();
                    if (selectedDeviceId !== requestDeviceId || selectedSystemName !== systemName || selectedFileCategory !== 'bios') {
                        if (currentSystemBiosPages[systemName]) currentSystemBiosPages[systemName] = { ...currentSystemBiosPages[systemName], loading: false };
                        return;
                    }
                    const rows = payload.bios || [];
                    const loadedRows = reset ? rows : [...(currentSystemBiosPages[systemName]?.bios || []), ...rows];
                    currentSystemBiosPages[systemName] = {
                        bios: loadedRows,
                        total: Number(payload.total ?? loadedRows.length),
                        nextOffset: offset + rows.length,
                        per_page: Number(payload.per_page || TREE_FILE_LOAD_SIZE),
                        loading: false,
                        error: false,
                    };
                } catch (error) {
                    console.error('Error loading system BIOS files:', error);
                    currentSystemBiosPages[systemName] = {
                        ...state,
                        bios: existingRows,
                        total: Number(state.total || 0),
                        nextOffset: offset,
                        loading: false,
                        error: true,
                    };
                }
                renderSystemBiosFilePage(systemName);
            }

            function renderSystemBiosFilePage(systemName) {
                const target = document.getElementById(`tree-system-bios-files-${cssSafeId(systemName)}`);
                if (!target) return;
                const payload = currentSystemBiosPages[systemName];
                if (!payload) {
                    target.innerHTML = '<div class="small text-muted tree-grid-empty">Select BIOS to load files.</div>';
                    return;
                }
                const rows = payload.bios || [];
                const total = Number(payload.total || rows.length);
                const loaded = rows.length;
                const hasMore = loaded < total;
                const firstLoad = payload.loading && !loaded;
                target.innerHTML = firstLoad
                    ? '<div class="tree-grid-empty small text-muted">Loading first 10 BIOS files...</div>'
                    : `
                        ${payload.error ? '<div class="alert alert-danger py-2 small mb-2">Unable to load BIOS files.</div>' : ''}
                        <div class="tree-leaf-list">
                            ${rows.map(row => {
                                const present = !!row.present_on_selected;
                                const sources = (row.devices || []).map(d => d.device_name || d.device_id).join(', ');
                                const path = row.file_path || row.bios_name || row.relative_path || '';
                                const sizeText = row.file_size ? formatBytes(row.file_size) : 'n/a';
                                const showSync = !present && row.devices && row.devices.length;
                                const rowPayload = encodeURIComponent(JSON.stringify(Object.assign({}, row)));
                                const tooltipParts = [row.bios_md5 ? `md5: ${row.bios_md5}` : 'no MD5 reported'];
                                if (sources) tooltipParts.push(sources);
                                return `
                                    <div class="tree-grid-row tree-leaf-row">
                                        <div class="tree-grid-main">
                                            <i class="bi bi-cpu tree-grid-icon"></i>
                                            <div class="tree-grid-label text-truncate" title="${escapeHtml(tooltipParts.join(' · '))}">
                                                <span class="fw-semibold">${escapeHtml(path)}</span>
                                            </div>
                                        </div>
                                        <div class="tree-grid-meta">${escapeHtml(sizeText)}</div>
                                        <div class="tree-grid-action">
                                            <span class="badge ${present ? 'text-bg-success' : (row.devices && row.devices.length ? 'text-bg-secondary' : 'text-bg-danger')}">${present ? 'Present' : (row.devices && row.devices.length ? 'Missing' : 'Unavailable')}</span>
                                            ${showSync ? `<button class="btn btn-primary btn-sm" onclick="syncBiosEncoded('${rowPayload}')">Sync</button>` : ''}
                                        </div>
                                    </div>
                                `;
                            }).join('') || '<div class="tree-grid-empty small text-muted">No BIOS files found for this system.</div>'}
                        </div>
                        <div class="tree-grid-more">
                            <span class="small text-muted">${total ? `Showing ${loaded.toLocaleString()} of ${total.toLocaleString()}` : 'No BIOS files reported'}</span>
                            <button class="btn btn-outline-primary btn-sm" type="button" ${!hasMore || payload.loading ? 'disabled' : ''} onclick="loadMoreSystemBiosFiles(${jsAttr(systemName)})">
                                ${payload.loading && loaded ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>' : '<i class="bi bi-plus-circle me-1"></i>'}
                                Show more
                            </button>
                        </div>
                    `;
            }

            function cssSafeId(value) {
                return btoa(unescape(encodeURIComponent(String(value)))).replace(/=+$/g, '').replace(/[^a-zA-Z0-9_-]/g, '_');
            }

            function displaySystemsTree() {
                const container = document.getElementById('systems-list');
                if (!container) {
                    return;
                }
                const entries = filteredSystemEntries();
                const showBiosRoot = Number(currentBiosSummary.total || 0) > 0 || selectedSystemName === BIOS_TREE_ROOT;
                if (!entries.length && !showBiosRoot) {
                    container.innerHTML = deviceRomSearchQuery
                        ? '<div class="empty-state">No systems, games, or BIOS files matched your search.</div>'
                        : renderDroneMetadataWaitingState('System, ROM, and BIOS metadata');
                    return;
                }
                entries.sort((a, b) => a[0].localeCompare(b[0]));
                const systemsTotal = Number(currentDeviceSystemsPage.total || entries.length);
                const biosTotal = Number(currentBiosSummary.total || 0);
                container.innerHTML = `
                    <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2 small text-muted">
                        <span>${systemsTotal.toLocaleString()} systems · ${biosTotal.toLocaleString()} BIOS files</span>
                    </div>
                    <div class="tree-grid">
                        ${entries.map(([systemName, summary]) => {
                            const count = Number(summary.rom_count || 0);
                            const active = selectedSystemName === systemName;
                            return `
                                <div class="tree-root ${active ? 'is-expanded' : ''}">
                                    <button type="button" class="tree-grid-row tree-root-row ${active ? 'is-active' : ''}"
                                    onclick="selectSystem(${jsAttr(systemName)})">
                                        <div class="tree-grid-main">
                                            <i class="bi ${active ? 'bi-chevron-down' : 'bi-chevron-right'} tree-grid-caret"></i>
                                            <i class="bi bi-folder2${active ? '-open' : ''} tree-grid-icon"></i>
                                            <div class="tree-grid-label"><span class="fw-semibold">${escapeHtml(systemName)}</span></div>
                                        </div>
                                        <div class="tree-grid-meta">${count.toLocaleString()} files</div>
                                    </button>
                                    ${active ? `
                                        <div class="tree-branch">
                                            <button type="button" class="tree-grid-row tree-category-row ${selectedFileCategory === 'games' ? 'is-active' : ''}" onclick="selectFileCategory(${jsAttr(systemName)}, 'games')">
                                                <div class="tree-grid-main">
                                                    <i class="bi bi-controller tree-grid-icon"></i>
                                                    <div class="tree-grid-label"><span class="fw-semibold">Games</span></div>
                                                </div>
                                                <div class="tree-grid-meta">${count.toLocaleString()} files</div>
                                            </button>
                                            ${selectedFileCategory === 'games' ? `
                                                <div id="tree-files-${cssSafeId(systemName)}" class="tree-files"></div>
                                                ${deviceAssetScope === 'missing' ? `<div class="px-2 pb-2"><button class="btn btn-outline-primary btn-sm" type="button" onclick="syncMissingSystemFiles(${jsAttr(systemName)})"><i class="bi bi-cloud-arrow-down me-1"></i>Sync all missing here</button></div>` : ''}
                                            ` : ''}
                                            <button type="button" class="tree-grid-row tree-category-row ${selectedFileCategory === 'bios' ? 'is-active' : ''}" onclick="selectFileCategory(${jsAttr(systemName)}, 'bios')">
                                                <div class="tree-grid-main">
                                                    <i class="bi bi-cpu tree-grid-icon"></i>
                                                    <div class="tree-grid-label"><span class="fw-semibold">BIOS</span></div>
                                                </div>
                                            </button>
                                            ${selectedFileCategory === 'bios' ? `<div id="tree-system-bios-files-${cssSafeId(systemName)}" class="tree-files"></div>` : ''}
                                        </div>
                                    ` : ''}
                                </div>
                            `;
                        }).join('')}
                        ${showBiosRoot ? `
                            <div class="tree-root ${selectedSystemName === BIOS_TREE_ROOT ? 'is-expanded' : ''}">
                                <button type="button" class="tree-grid-row tree-root-row ${selectedSystemName === BIOS_TREE_ROOT ? 'is-active' : ''}" onclick="selectBiosRoot()">
                                    <div class="tree-grid-main">
                                        <i class="bi ${selectedSystemName === BIOS_TREE_ROOT ? 'bi-chevron-down' : 'bi-chevron-right'} tree-grid-caret"></i>
                                        <i class="bi bi-folder2${selectedSystemName === BIOS_TREE_ROOT ? '-open' : ''} tree-grid-icon"></i>
                                        <div class="tree-grid-label"><span class="fw-semibold">Shared / Unassigned BIOS</span></div>
                                    </div>
                                    <div class="tree-grid-meta">${biosTotal.toLocaleString()} files</div>
                                </button>
                                ${selectedSystemName === BIOS_TREE_ROOT ? `
                                    <div class="tree-branch">
                                        <button type="button" class="tree-grid-row tree-category-row ${selectedFileCategory === 'bios' ? 'is-active' : ''}" onclick="selectFileCategory(${jsAttr(BIOS_TREE_ROOT)}, 'bios')">
                                            <div class="tree-grid-main">
                                                <i class="bi bi-cpu tree-grid-icon"></i>
                                                <div class="tree-grid-label"><span class="fw-semibold">BIOS files</span></div>
                                            </div>
                                            <div class="tree-grid-meta">${biosTotal.toLocaleString()} files</div>
                                        </button>
                                        <div id="tree-files-${cssSafeId(BIOS_TREE_ROOT)}" class="tree-files"></div>
                                    </div>
                                ` : ''}
                            </div>
                        ` : ''}
                    </div>
                `;
                if (selectedSystemName === BIOS_TREE_ROOT) {
                    renderBiosFilePage();
                } else if (selectedSystemName && selectedFileCategory === 'bios') {
                    renderSystemBiosFilePage(selectedSystemName);
                } else if (selectedSystemName) {
                    renderSystemRomPage(selectedSystemName);
                }
            }

            function renderDroneMetadataWaitingState(label) {
                return `
                    <div class="empty-state d-flex flex-column align-items-center justify-content-center gap-2 py-4">
                        <div class="spinner-border text-primary" role="status" aria-hidden="true"></div>
                        <div>${label === 'ROM metadata' ? 'Waiting for Drone to upload artwork metadata' : `Waiting for Drone to upload ${escapeHtml(label)}`}</div>
                    </div>
                `;
            }

            function selectedDrone() {
                return currentDevices.find(d => d.device_id === selectedDeviceId) || null;
            }

            function automationValuesMatch(reported, desired) {
                if (!reported || !desired) return false;
                return Object.entries(desired).every(([key, value]) => reported[key] === value);
            }

            function applyPendingDeviceAutomation(device) {
                if (!device || !device.device_id) return device;
                const pending = pendingDeviceAutomation[device.device_id];
                if (!pending) return device;
                const systemInfo = { ...(device.system_info || {}) };
                Object.entries(pending).forEach(([field, desired]) => {
                    if (automationValuesMatch(systemInfo[field], desired)) {
                        delete pending[field];
                    } else {
                        systemInfo[field] = { ...desired, pending: true };
                    }
                });
                if (!Object.keys(pending).length) delete pendingDeviceAutomation[device.device_id];
                return { ...device, system_info: systemInfo };
            }

            function rememberPendingDeviceAutomation(field, desired) {
                if (!selectedDeviceId) return;
                pendingDeviceAutomation[selectedDeviceId] = {
                    ...(pendingDeviceAutomation[selectedDeviceId] || {}),
                    [field]: { ...desired },
                };
                const index = currentDevices.findIndex(item => item.device_id === selectedDeviceId);
                if (index >= 0) currentDevices[index] = applyPendingDeviceAutomation(currentDevices[index]);
            }

            async function refreshSelectedDroneDetails() {
                if (!selectedDeviceId) return null;
                const response = await apiGet(`/api/devices/${selectedDeviceId}?include_inventory=false&include_configs=false`, { showLoader: false });
                if (!response.ok) throw new Error('Failed to load device details');
                const device = applyPendingDeviceAutomation(await response.json());
                const index = currentDevices.findIndex(item => item.device_id === device.device_id);
                if (index >= 0) {
                    currentDevices[index] = { ...currentDevices[index], ...device };
                } else {
                    currentDevices.push(device);
                }
                updateSelectedDeviceSummary();
                return device;
            }

            function renderDeviceAdminPanel() {
                const container = document.getElementById('device-admin-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const info = device.system_info || {};
                const screenMode = ['full', 'kiosk', 'kid'].includes(String(info.screen_mode || '').toLowerCase())
                    ? String(info.screen_mode).toLowerCase()
                    : null;
                const volumeKnown = Number.isFinite(Number(info.audio_volume));
                const currentVolume = volumeKnown ? Number(info.audio_volume) : null;
                const volumePresets = [
                    { level: 0, label: 'Mute', icon: 'bi-volume-mute' },
                    { level: 25, label: '25%', icon: 'bi-volume-down' },
                    { level: 50, label: '50%', icon: 'bi-volume-down' },
                    { level: 75, label: '75%', icon: 'bi-volume-up' },
                    { level: 100, label: '100%', icon: 'bi-volume-up' },
                ];
                // Highlight the preset closest to the reported volume.
                let nearestPreset = null;
                if (volumeKnown) {
                    nearestPreset = volumePresets.reduce((best, p) =>
                        Math.abs(p.level - currentVolume) < Math.abs(best.level - currentVolume) ? p : best, volumePresets[0]).level;
                }
                const volumeButtons = volumePresets.map(p => `
                    <button class="btn btn-sm ${nearestPreset === p.level ? 'btn-primary' : 'btn-outline-primary'}" type="button" data-volume-level="${p.level}"
                        onclick="queueDeviceVolume(${p.level})"><i class="bi ${p.icon} me-1"></i>${p.label}</button>
                `).join('');
                const screenModeButtons = [
                    { mode: 'full', label: 'Full', icon: 'bi-unlock' },
                    { mode: 'kiosk', label: 'Kiosk', icon: 'bi-lock' },
                    { mode: 'kid', label: 'Kid', icon: 'bi-person' },
                ].map(item => `
                    <button class="btn btn-sm ${screenMode === item.mode ? 'btn-primary' : 'btn-outline-primary'}" type="button" data-screen-mode="${item.mode}"
                        onclick="queueDeviceScreenMode('${item.mode}')"><i class="bi ${item.icon} me-1"></i>${item.label}</button>
                `).join('');
                container.innerHTML = `
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
                            <strong><i class="bi bi-display me-1"></i>Screen Mode</strong>
                            <span class="small text-muted" data-device-admin-field="screen-mode">Current: ${screenMode || 'not yet reported'}</span>
                        </div>
                        <div class="btn-group bff-segmented" role="group" aria-label="Screen mode">${screenModeButtons}</div>
                        <div class="small text-muted mt-2">Changing screen mode restarts EmulationStation on the device.</div>
                    </div></div>
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
                            <strong><i class="bi bi-volume-up me-1"></i>Volume</strong>
                            <span class="small text-muted" data-device-admin-field="volume">Current: ${volumeKnown ? (currentVolume <= 0 ? 'muted' : currentVolume + '%') : 'not yet reported'}</span>
                        </div>
                        <div class="btn-group flex-wrap" role="group" aria-label="Volume presets">${volumeButtons}</div>
                    </div></div>
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
                            <strong><i class="bi bi-music-note-beamed me-1"></i>Music Volume</strong>
                        </div>
                        <div class="small text-muted mb-2">EmulationStation's background music volume. Applying this restarts EmulationStation on the device.</div>
                        <div class="btn-group flex-wrap" role="group" aria-label="Music volume presets">${volumePresets.map(p => `
                            <button class="btn btn-sm btn-outline-primary" type="button" onclick="queueDeviceMusicVolume(${p.level})"><i class="bi ${p.icon} me-1"></i>${p.label}</button>
                        `).join('')}</div>
                    </div></div>
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
                            <strong><i class="bi bi-moon-stars me-1"></i>Screensaver</strong>
                        </div>
                        <div class="small text-muted mb-2">How long EmulationStation waits with no input before starting the screensaver. Applying this restarts EmulationStation on the device.</div>
                        <div class="row g-2 align-items-end">
                            <div class="col-sm-8">
                                <label class="form-label small mb-1" for="screensaver-minutes">Start screensaver after (minutes, 0 = disabled)</label>
                                <input class="form-control form-control-sm" type="number" id="screensaver-minutes" min="0" max="120" step="1" value="5">
                            </div>
                            <div class="col-sm-4">
                                <button class="btn btn-primary btn-sm w-100" type="button" onclick="queueDeviceScreensaver()"><i class="bi bi-save me-1"></i>Save</button>
                            </div>
                        </div>
                    </div></div>
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
                            <strong><i class="bi bi-collection-play me-1"></i>Game Collections &amp; Systems</strong>
                            <button class="btn btn-outline-secondary btn-sm" type="button" onclick="loadDeviceEsCollections()"><i class="bi bi-arrow-clockwise me-1"></i>Load from Drone</button>
                        </div>
                        <div class="small text-muted mb-2">Which systems appear, which are grouped together, and which automatic/custom collections are enabled on the Drone. Loading and saving both restart EmulationStation on the device.</div>
                        <div id="es-collections-body"><div class="text-muted small">Click "Load from Drone" to fetch the current configuration.</div></div>
                    </div></div>
                    ${renderIdleVolumeCard(info)}
                    ${renderIdleGameExitCard(info)}
                    ${renderWifiRecoveryCard(info)}
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <strong><i class="bi bi-power me-1"></i>Power</strong>
                        <div class="mt-2">
                            <button class="btn btn-outline-danger btn-sm" type="button"
                                onclick="queueDeviceAction('restart')"><i class="bi bi-arrow-clockwise me-1"></i>Restart Machine</button>
                        </div>
                    </div></div>
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <strong><i class="bi bi-database-gear me-1"></i>Asset Cache</strong>
                        <div class="d-flex flex-wrap gap-2 mt-2">
                            <button class="btn btn-outline-primary btn-sm" type="button"
                                onclick="queueDeviceAction('rebuild_asset_metadata')"><i class="bi bi-database-up me-1"></i>Rebuild Asset Metadata</button>
                            <button class="btn btn-outline-warning btn-sm" type="button"
                                onclick="queueDeviceAction('purge_asset_cache')"><i class="bi bi-trash me-1"></i>Purge Asset Cache</button>
                        </div>
                        <div class="small text-muted mt-2">Rebuild re-scans and re-uploads a fresh inventory; purge keeps fingerprints and forces a full re-scan.</div>
                    </div></div>
                    ${info.pixen_installed === true ? `
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <strong><i class="bi bi-play-circle me-1"></i>PixeN</strong>
                        <div class="d-flex flex-wrap gap-2 mt-2">
                            <button class="btn btn-outline-success btn-sm" type="button"
                                onclick="queueDeviceAction('run_pixen_update')"><i class="bi bi-play-circle me-1"></i>Run PixeN Update</button>
                        </div>
                        <div class="small text-muted mt-2">Runs the installed PixeN upgrade script on the selected Drone.</div>
                    </div></div>
                    ` : ""}
                    <div class="card"><div class="card-body py-3">
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                            <strong><i class="bi bi-list-check me-1"></i>Recent Actions</strong>
                            <div class="d-flex align-items-center gap-2">
                                <button class="btn btn-outline-secondary btn-sm" type="button" onclick="loadDeviceActions()"><i class="bi bi-arrow-clockwise me-1"></i>Refresh</button>
                                <button class="btn btn-outline-danger btn-sm mutate-only" type="button" onclick="deleteDeviceActions()"><i class="bi bi-x-circle me-1"></i>Clear Queued</button>
                            </div>
                        </div>
                        <div class="small text-muted mb-2">Queued and in-progress actions, plus actions the Drone completed in the last hour. Drones poll for actions periodically, so a newly queued action may stay "pending" for up to a minute.</div>
                        <div id="actions-list"></div>
                    </div></div>
                `;
                applyRbacUI();
                loadDeviceActions();
            }

            function updateDeviceAdminStatusInPlace() {
                const container = document.getElementById('device-admin-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const info = device.system_info || {};
                const screenMode = ['full', 'kiosk', 'kid'].includes(String(info.screen_mode || '').toLowerCase())
                    ? String(info.screen_mode).toLowerCase()
                    : null;
                const volumeKnown = Number.isFinite(Number(info.audio_volume));
                const currentVolume = volumeKnown ? Number(info.audio_volume) : null;
                const volumeLevels = [0, 25, 50, 75, 100];
                const nearestVolume = volumeKnown
                    ? volumeLevels.reduce((best, level) =>
                        Math.abs(level - currentVolume) < Math.abs(best - currentVolume) ? level : best, volumeLevels[0])
                    : null;
                const screenStatus = container.querySelector('[data-device-admin-field="screen-mode"]');
                const volumeStatus = container.querySelector('[data-device-admin-field="volume"]');
                if (screenStatus) screenStatus.textContent = `Current: ${screenMode || 'not yet reported'}`;
                if (volumeStatus) volumeStatus.textContent = `Current: ${volumeKnown ? (currentVolume <= 0 ? 'muted' : currentVolume + '%') : 'not yet reported'}`;
                container.querySelectorAll('[data-screen-mode]').forEach(button => {
                    const active = button.dataset.screenMode === screenMode;
                    button.classList.toggle('btn-primary', active);
                    button.classList.toggle('btn-outline-primary', !active);
                });
                container.querySelectorAll('[data-volume-level]').forEach(button => {
                    const active = Number(button.dataset.volumeLevel) === nearestVolume;
                    button.classList.toggle('btn-primary', active);
                    button.classList.toggle('btn-outline-primary', !active);
                });
            }

            async function queueDeviceVolume(level) {
                await queueDeviceAction('set_volume', { payload: { level } });
            }

            async function queueDeviceMusicVolume(level) {
                await queueDeviceAction('set_music_volume', { payload: { level } });
            }

            async function queueDeviceScreensaver() {
                const minutes = parseInt(document.getElementById('screensaver-minutes')?.value, 10);
                if (!Number.isFinite(minutes) || minutes < 0 || minutes > 120) {
                    showMessage('Screensaver delay must be between 0 and 120 minutes.', 'danger');
                    return;
                }
                await queueDeviceAction('set_es_collections', {
                    confirm: false,
                    payload: { screensaver_minutes: minutes },
                });
            }

            function renderIdleVolumeCard(info) {
                const automation = (info && typeof info.idle_volume_automation === 'object' && info.idle_volume_automation) || null;
                const reported = !!automation;
                const enabled = reported ? !!automation.enabled : false;
                const idleMinutes = reported && Number.isFinite(Number(automation.idle_minutes)) ? Number(automation.idle_minutes) : 5;
                const targetVolume = reported && Number.isFinite(Number(automation.target_volume)) ? Number(automation.target_volume) : 25;
                const current = !reported
                    ? 'not yet reported'
                    : (enabled
                        ? `${automation.pending ? 'pending — ' : 'on — '}set to ${targetVolume}% after ${idleMinutes} min idle`
                        : 'off');
                return `
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
                            <strong><i class="bi bi-volume-down me-1"></i>Idle Volume Automation</strong>
                            <span class="small text-muted" data-device-admin-field="idle-volume">Current: ${escapeHtml(current)}</span>
                        </div>
                        <div class="small text-muted mb-2">Set the Drone's output volume to a target level (raising or lowering it, whichever the target requires) after a period with no controller or keyboard input. The volume stays at the target until the device is used again.</div>
                        <div class="form-check form-switch mb-2">
                            <input class="form-check-input" type="checkbox" role="switch" id="idle-volume-enabled" ${enabled ? 'checked' : ''}>
                            <label class="form-check-label" for="idle-volume-enabled">Enable idle volume lowering</label>
                        </div>
                        <div class="row g-2 align-items-end">
                            <div class="col-sm-5">
                                <label class="form-label small mb-1" for="idle-volume-minutes">Idle minutes</label>
                                <input class="form-control form-control-sm" type="number" id="idle-volume-minutes" min="1" max="1440" step="1" value="${idleMinutes}">
                            </div>
                            <div class="col-sm-5">
                                <label class="form-label small mb-1" for="idle-volume-target">Target volume (%)</label>
                                <input class="form-control form-control-sm" type="number" id="idle-volume-target" min="0" max="100" step="5" value="${targetVolume}">
                            </div>
                            <div class="col-sm-2">
                                <button class="btn btn-primary btn-sm w-100" type="button" onclick="queueDeviceIdleVolume()"><i class="bi bi-save me-1"></i>Save</button>
                            </div>
                        </div>
                        <div class="small text-muted mt-2">0 = mute. Applied on the Drone within about a minute.</div>
                    </div></div>
                `;
            }

            async function queueDeviceIdleVolume() {
                const enabled = !!document.getElementById('idle-volume-enabled')?.checked;
                const idleMinutes = parseInt(document.getElementById('idle-volume-minutes')?.value, 10);
                const targetVolume = parseInt(document.getElementById('idle-volume-target')?.value, 10);
                if (!Number.isFinite(idleMinutes) || idleMinutes < 1 || idleMinutes > 1440) {
                    showMessage('Idle minutes must be between 1 and 1440.', 'danger');
                    return;
                }
                if (!Number.isFinite(targetVolume) || targetVolume < 0 || targetVolume > 100) {
                    showMessage('Target volume must be between 0 and 100.', 'danger');
                    return;
                }
                const desired = { enabled, idle_minutes: idleMinutes, target_volume: targetVolume };
                await queueDeviceAction('set_idle_volume_automation', {
                    confirm: false,
                    payload: desired,
                });
                rememberPendingDeviceAutomation('idle_volume_automation', desired);
                renderDeviceAdminPanel();
            }

            function renderIdleGameExitCard(info) {
                const automation = (info && typeof info.idle_game_exit_automation === 'object' && info.idle_game_exit_automation) || null;
                const reported = !!automation;
                const enabled = reported ? !!automation.enabled : false;
                const idleMinutes = reported && Number.isFinite(Number(automation.idle_minutes)) ? Number(automation.idle_minutes) : 15;
                const current = !reported
                    ? 'not yet reported'
                    : (enabled
                        ? `${automation.pending ? 'pending — ' : 'on — '}exit the game after ${idleMinutes} min idle`
                        : 'off');
                return `
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
                            <strong><i class="bi bi-power me-1"></i>Idle Game Exit Automation</strong>
                            <span class="small text-muted" data-device-admin-field="idle-game-exit">Current: ${escapeHtml(current)}</span>
                        </div>
                        <div class="small text-muted mb-2">Exit the running game and return to EmulationStation after it has gone without any controller or keyboard input for a set amount of time. Only applies while a game is actually running.</div>
                        <div class="form-check form-switch mb-2">
                            <input class="form-check-input" type="checkbox" role="switch" id="idle-game-exit-enabled" ${enabled ? 'checked' : ''}>
                            <label class="form-check-label" for="idle-game-exit-enabled">Enable idle game exit</label>
                        </div>
                        <div class="row g-2 align-items-end">
                            <div class="col-sm-5">
                                <label class="form-label small mb-1" for="idle-game-exit-minutes">Idle minutes</label>
                                <input class="form-control form-control-sm" type="number" id="idle-game-exit-minutes" min="1" max="1440" step="1" value="${idleMinutes}">
                            </div>
                            <div class="col-sm-2">
                                <button class="btn btn-primary btn-sm w-100" type="button" onclick="queueDeviceIdleGameExit()"><i class="bi bi-save me-1"></i>Save</button>
                            </div>
                        </div>
                        <div class="small text-muted mt-2">Applied on the Drone within about a minute.</div>
                    </div></div>
                `;
            }

            async function queueDeviceIdleGameExit() {
                const enabled = !!document.getElementById('idle-game-exit-enabled')?.checked;
                const idleMinutes = parseInt(document.getElementById('idle-game-exit-minutes')?.value, 10);
                if (!Number.isFinite(idleMinutes) || idleMinutes < 1 || idleMinutes > 1440) {
                    showMessage('Idle minutes must be between 1 and 1440.', 'danger');
                    return;
                }
                const desired = { enabled, idle_minutes: idleMinutes };
                await queueDeviceAction('set_idle_game_exit_automation', {
                    confirm: false,
                    payload: desired,
                });
                rememberPendingDeviceAutomation('idle_game_exit_automation', desired);
                renderDeviceAdminPanel();
            }

            function renderWifiRecoveryCard(info) {
                const automation = (info && typeof info.wifi_recovery_automation === 'object' && info.wifi_recovery_automation) || null;
                const reported = !!automation;
                const enabled = reported ? !!automation.enabled : false;
                const current = !reported
                    ? 'not yet reported'
                    : (enabled ? `${automation.pending ? 'pending - ' : ''}on` : 'off');
                return `
                    <div class="card mb-3 mutate-only"><div class="card-body py-3">
                        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
                            <strong><i class="bi bi-wifi me-1"></i>Wi-Fi Recovery Automation</strong>
                            <span class="small text-muted" data-device-admin-field="wifi-recovery">Current: ${escapeHtml(current)}</span>
                        </div>
                        <div class="small text-muted mb-2">Every 60 seconds, verify that Wi-Fi is enabled and connected. A failed check disables Wi-Fi, waits three seconds, and enables it again.</div>
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                            <div class="form-check form-switch mb-0">
                                <input class="form-check-input" type="checkbox" role="switch" id="wifi-recovery-enabled" ${enabled ? 'checked' : ''}>
                                <label class="form-check-label" for="wifi-recovery-enabled">Enable Wi-Fi recovery</label>
                            </div>
                            <button class="btn btn-primary btn-sm" type="button" onclick="queueDeviceWifiRecovery()"><i class="bi bi-save me-1"></i>Save</button>
                        </div>
                        <div class="small text-muted mt-2">Applied on the Drone within about a minute.</div>
                    </div></div>
                `;
            }

            async function queueDeviceWifiRecovery() {
                const desired = { enabled: !!document.getElementById('wifi-recovery-enabled')?.checked };
                await queueDeviceAction('set_wifi_recovery_automation', {
                    confirm: false,
                    payload: desired,
                });
                rememberPendingDeviceAutomation('wifi_recovery_automation', desired);
                renderDeviceAdminPanel();
            }

            async function queueDeviceScreenMode(mode) {
                await queueDeviceAction('set_screen_mode', { payload: { mode } });
            }

            function renderDroneNetworkPanel() {
                const container = document.getElementById('drone-network-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const resolved = device.resolved_network || {};
                const ipv4 = resolved.ipv4 || [];
                const ipv6 = resolved.ipv6 || [];
                const publicIp = (device.network || {}).public_ip || (device.network || {}).public || 'n/a';
                const publicIpStatus = device.peer_resolvable ? ' (peer-resolvable)' : '';
                const cert = device.certificate || {};
                const peerChecks = device.peer_checks || [];
                const peerResolvedBy = device.peer_resolved_by || [];
                const info = device.system_info || {};
                const screenMode = ['full', 'kiosk', 'kid'].includes(String(info.screen_mode || '').toLowerCase())
                    ? String(info.screen_mode).toLowerCase()
                    : null;
                const volumeKnown = Number.isFinite(Number(info.audio_volume));
                const systemRows = [
                    ['Hostname', info.hostname || device.device_name],
                    ['OS', [info.os, info.os_release].filter(Boolean).join(' ')],
                    ['Batocera', info.batocera_version],
                    ['Drone App', info.drone_app_version],
                    ['Architecture', info.architecture],
                    ['CPU', info.cpu ? `${info.cpu.model || 'CPU'} ${info.cpu.count ? `(${info.cpu.count} cores)` : ''}` : ''],
                    ['Memory', info.memory ? `${info.memory.available || 'n/a'} available / ${info.memory.total || 'n/a'} total` : ''],
                    ['Storage', info.disk && info.disk.free_bytes ? `${(Number(info.disk.free_bytes) / 1024 / 1024 / 1024).toFixed(1)} GiB free` : ''],
                    ['Screen Mode', screenMode || 'unknown'],
                    ['Volume', volumeKnown ? (Number(info.audio_volume) <= 0 ? 'muted' : `${Number(info.audio_volume)}%`) : 'unknown'],
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
                        <div class="small text-muted">Public IP: ${escapeHtml(publicIp)}${publicIpStatus}</div>
                        <div class="small text-muted">API: ${escapeHtml(device.reachable_url || `${device.scheme || 'https'}://${ipv4[0] || device.device_id}`)}</div>
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
                        <strong>Performance Metrics</strong>
                        <div class="mt-2">${renderMetricsGrid(info.performance || {})}</div>
                        <hr>
                        <strong>Outbound Peer Checks (from this drone)</strong>
                        ${latestPeers.length ? latestPeers.map(check => `
                            <div class="mt-2 p-2 rounded border">
                                <div class="d-flex justify-content-between gap-2">
                                    <span class="small">${escapeHtml(check.target_name || check.target_drone_id || 'Peer Drone')}</span>
                                    <span class="badge ${check.status === 'pass' ? 'text-bg-success' : 'text-bg-danger'}">${check.status === 'pass' ? 'RESOLVED' : 'FAILED'}</span>
                                </div>
                                <div class="small text-muted">${escapeHtml(check.target_address || 'n/a')} · ${escapeHtml(check.checked_at || 'n/a')} · ${check.latency_ms ?? 'n/a'} ms</div>
                                ${check.failure_reason ? `<div class="small text-danger">${escapeHtml(check.failure_reason)}</div>` : ''}
                            </div>
                        `).join('') : '<div class="small text-muted mt-1">No outbound peer checks reported yet.</div>'}
                        <hr>
                        <strong>Resolved By (drones that reached this drone)</strong>
                        ${peerResolvedBy.length ? peerResolvedBy.map(r => `
                            <div class="mt-2 p-2 rounded border">
                                <div class="d-flex justify-content-between gap-2">
                                    <span class="small">${escapeHtml(r.source_name || r.source_drone_id || 'Peer Drone')}</span>
                                    <span class="badge text-bg-success">RESOLVED</span>
                                </div>
                                <div class="small text-muted">${escapeHtml(r.target_address || 'n/a')} · ${escapeHtml(r.checked_at || 'n/a')} · ${r.latency_ms ?? 'n/a'} ms</div>
                            </div>
                        `).join('') : '<div class="small text-muted mt-1">No drones have resolved this drone yet.</div>'}
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

            async function readErrorDetail(response, fallback) {
                try {
                    const body = await response.json();
                    return body && body.detail ? String(body.detail) : fallback;
                } catch (e) {
                    return fallback;
                }
            }

            async function syncRom(row) {
                try {
                    const response = await apiPost(`/api/devices/${selectedDeviceId}/sync-rom`, row);
                    if (!response.ok) throw new Error(await readErrorDetail(response, 'Failed to queue ROM sync.'));
                    showMessage('Download queued. The selected Drone will choose the source peer automatically.', 'success');
                    // Refresh the current leaf list so the Sync button disappears once the Drone reports the ROM
                    const systemName = row.system_name || row.system || '';
                    if (systemName && selectedSystemName === systemName && selectedFileCategory === 'games') {
                        await loadSystemRomPage(systemName, { reset: true });
                    }
                } catch (error) {
                    console.error('Error queuing ROM sync:', error);
                    showMessage(error.message || 'Failed to queue ROM sync.', 'error');
                }
            }

            async function syncSystem(systemName) {
                const response = await apiPost(`/api/devices/${selectedDeviceId}/sync-system`, { system_name: systemName });
                if (!response.ok) throw new Error(await readErrorDetail(response, 'Failed to queue system sync.'));
                showMessage('System sync queued. The Drone will choose source peers automatically.', 'success');
            }

            function syncBiosEncoded(encodedPayload) {
                try {
                    syncBios(JSON.parse(decodeURIComponent(encodedPayload || '')));
                } catch (error) {
                    console.error('Error parsing BIOS sync payload:', error);
                    showMessage('Failed to queue BIOS sync.', 'error');
                }
            }

            async function syncBios(row) {
                try {
                    const response = await apiPost(`/api/devices/${selectedDeviceId}/sync-bios`, row);
                    if (!response.ok) throw new Error(await readErrorDetail(response, 'Failed to queue BIOS sync.'));
                    showMessage('BIOS sync queued. The Drone will choose the source peer automatically.', 'success');
                    await loadBiosSummary();
                    if (selectedSystemName === BIOS_TREE_ROOT) {
                        await loadBiosFilePage({ reset: true });
                    } else {
                        displaySystemsTree();
                    }
                } catch (error) {
                    console.error('Error queuing BIOS sync:', error);
                    showMessage(error.message || 'Failed to queue BIOS sync.', 'error');
                }
            }

            function normalizeArtworkSelection(values, allValue) {
                const unique = [];
                (values || []).forEach(value => {
                    const text = String(value || '').trim();
                    if (text && text !== allValue && !unique.includes(text)) unique.push(text);
                });
                return unique;
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

            function updateSelectedDeviceHeader() {
                const title = document.getElementById('selected-device-title');
                const idNode = document.getElementById('selected-device-id');
                const device = currentDevices.find(d => d.device_id === selectedDeviceId);
                if (title) title.textContent = device ? device.device_name : 'Selected Drone';
                if (idNode) idNode.textContent = selectedDeviceId
                    ? `Drone ID: ${device ? device.device_id : selectedDeviceId}`
                    : '';
            }

            function updateSelectedDeviceWorkspace() {
                const workspace = document.getElementById('selected-device-workspace');
                const listView = document.getElementById('device-list-view');
                if (!workspace) return;
                if (!selectedDeviceId) {
                    workspace.style.display = 'none';
                    if (listView) listView.style.display = 'block';
                    updateSelectedDeviceHeader();
                    return;
                }
                if (listView) listView.style.display = 'none';
                workspace.style.display = 'block';
                updateSelectedDeviceHeader();
                renderDroneNetworkPanel();
                renderDroneSpeedPanel();
            }

            function backToDevices() {
                selectedDeviceId = null;
                currentDeviceView = 'overview';
                stopSelectedDeviceDataAutoRefresh();
                setRoute('devices', null, 'systems');
            }

            function stopSelectedDeviceDataAutoRefresh() {
                if (selectedDeviceDataRefreshTimer) clearInterval(selectedDeviceDataRefreshTimer);
                selectedDeviceDataRefreshTimer = null;
            }

            function startSelectedDeviceDataAutoRefresh(viewName) {
                stopSelectedDeviceDataAutoRefresh();
                if (!selectedDeviceId) return;
                // The Admin actions list refreshes more often so an operator can watch a
                // queued action move pending -> in_progress -> completed without reloading.
                if (viewName === 'admin') {
                    selectedDeviceDataRefreshTimer = setInterval(() => {
                        if (!selectedDeviceId || currentTab !== 'devices' || currentDeviceView !== 'admin') {
                            stopSelectedDeviceDataAutoRefresh();
                            return;
                        }
                        loadDeviceActions({showLoader: false});
                    }, 8000);
                    return;
                }
                // Only the Admin actions list auto-refreshes; every other device view is
                // static or manually refreshed.
            }

            function switchDeviceView(viewName, buttonEl = null, updateUrl = true) {
                if (!selectedDeviceId) return;
                currentDeviceView = ['overview', 'systems', 'admin'].includes(viewName) ? viewName : (viewName === 'bios' ? 'systems' : 'overview');
                if (updateUrl) {
                    setRoute('devices', selectedDeviceId, currentDeviceView);
                    return;
                }
                startSelectedDeviceDataAutoRefresh(currentDeviceView);
                document.querySelectorAll('.device-view-btn').forEach(btn => btn.classList.remove('active'));
                const activeBtn = buttonEl || document.querySelector(`.device-view-btn[data-device-view="${currentDeviceView}"]`);
                if (activeBtn) activeBtn.classList.add('active');

                const overviewPanel = document.getElementById('device-overview-panel');
                const systemsPanel = document.getElementById('device-systems-panel');
                const adminPanel = document.getElementById('device-admin-panel');
                if (overviewPanel) overviewPanel.style.display = currentDeviceView === 'overview' ? 'block' : 'none';
                if (systemsPanel) systemsPanel.style.display = currentDeviceView === 'systems' ? 'block' : 'none';
                if (adminPanel) adminPanel.style.display = currentDeviceView === 'admin' ? 'block' : 'none';

                if (currentDeviceView === 'overview') {
                    renderDroneNetworkPanel();
                    renderDroneSpeedPanel();
                }
                if (currentDeviceView === 'systems') {
                    loadDeviceSystemsView();
                }
                if (currentDeviceView === 'admin') {
                    renderDeviceAdminPanel();
                    refreshSelectedDroneDetails()
                        .then(() => updateDeviceAdminStatusInPlace())
                        .catch(error => console.error('Error refreshing selected Drone details:', error));
                }
                if (actionRefreshTimer) clearInterval(actionRefreshTimer);
                actionRefreshTimer = null;
                applyRbacUI();
            }

            // --- Hash query-string navigation state -------------------------------
            // The route path (#/devices/device/{id}/{view}) identifies the screen;
            // a query string appended to the hash (…?sp=2&sq=mario) carries the
            // page / search / filter / selection for whatever list is on screen so
            // the browser location reflects every navigation and Back/Forward and
            // refresh restore the exact spot. Paging/search/filter use replaceState
            // (no history flood); drilling into a device/detail uses a real history
            // step via setRoute, so Back returns to the list at the page you left.
            function getRouteQuery() {
                const raw = window.location.hash || '';
                const qIndex = raw.indexOf('?');
                return new URLSearchParams(qIndex >= 0 ? raw.slice(qIndex + 1) : '');
            }
            // Page-number params are omitted from the URL when they equal 1 so a
            // first page produces a clean hash; non-page params (searches, filters,
            // selections) are only dropped when blank.
            const ROUTE_PAGE_KEYS = new Set(['sp', 'dsp', 'ap', 'mp']);
            function updateRouteQuery(updates, options = {}) {
                const raw = window.location.hash || '#/devices';
                const qIndex = raw.indexOf('?');
                const path = qIndex >= 0 ? raw.slice(0, qIndex) : raw;
                const params = new URLSearchParams(qIndex >= 0 ? raw.slice(qIndex + 1) : '');
                Object.entries(updates).forEach(([key, value]) => {
                    const blank = value === null || value === undefined || value === '';
                    const defaultPage = ROUTE_PAGE_KEYS.has(key) && (value === 1 || value === '1');
                    if (blank || defaultPage) {
                        params.delete(key);
                    } else {
                        params.set(key, String(value));
                    }
                });
                const qs = params.toString();
                const nextHash = qs ? `${path}?${qs}` : path;
                if (window.location.hash === nextHash) return;
                if (options.push) {
                    window.location.hash = nextHash; // new history entry; triggers hashchange
                } else {
                    history.replaceState(null, '', nextHash); // update URL in place, no re-route
                }
            }
            function scrollAppToTop() {
                // Reset scroll on navigation/paging so the viewport never stays parked
                // at the bottom of the previous content.
                try {
                    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
                } catch (e) {
                    window.scrollTo(0, 0);
                }
                ['.dashboard-content', '.app-main', '#app'].forEach(sel => {
                    const node = document.querySelector(sel);
                    if (node) node.scrollTop = 0;
                });
            }
            // Restore the on-screen list state (page/search/filter/selection) from the
            // hash query before the panel loaders run, so Back/Forward/refresh land on
            // the same page. Absent params reset to defaults, so switching screens
            // (which produces a query-less hash) clears stale paging.
            function hydrateNavStateFromHash() {
                const q = getRouteQuery();
                const intParam = (key) => Math.max(1, Number.parseInt(q.get(key) || '1', 10) || 1);
                deviceSystemsPage = intParam('dsp');
                deviceRomSearchQuery = q.get('rq') || '';
                deviceAssetScope = ['all', 'missing'].includes(q.get('sc')) ? q.get('sc') : 'mine';
                selectedSystemName = q.get('syn') || null;
                selectedFileCategory = q.get('fc') || (selectedSystemName === BIOS_TREE_ROOT ? 'bios' : (selectedSystemName ? 'games' : null));
                swarmMasterPage = intParam('mp');
                notificationsPageOffset = Math.max(0, Number.parseInt(q.get('np') || '0', 10) || 0);
            }

            function setRoute(tabName, deviceId = selectedDeviceId, deviceView = currentDeviceView, swarmView = null) {
                let hash = `#/${tabName}`;
                if (tabName === 'devices') {
                    const swarmPath = isSharedSwarmSelected() ? `/swarm/${encodeURIComponent(selectedSwarmId)}` : '';
                    if (!deviceId && swarmView) hash = `#/devices${swarmPath}/swarm/${swarmView}`;
                    if (deviceId) hash = `#/devices${swarmPath}/device/${encodeURIComponent(deviceId)}/${deviceView || 'overview'}`;
                }
                if (window.location.hash !== hash) window.location.hash = hash; else applyRouteFromHash();
            }

            function normalizeDeviceView(viewName) {
                if (viewName === 'actions' || viewName === 'metadata') return 'overview';
                if (viewName === 'bios') return 'systems';
                return ['overview', 'systems', 'admin'].includes(viewName) ? viewName : 'overview';
            }

            function parseRoute() {
                const raw = window.location.hash || '#/devices';
                const clean = raw.replace(/^#\/?/, '');
                const parts = clean.split('/').filter(Boolean);
                const allowed = ['devices', 'hive', 'profile', 'notifications', 'super-admin', 'help'];
                if ((parts[0] === 'overview' || parts[0] === 'systems' || parts[0] === 'bios' || parts[0] === 'gamelogs' || parts[0] === 'configs' || parts[0] === 'actions' || parts[0] === 'metadata' || parts[0] === 'admin') && parts[1]) {
                    return { tab: 'devices', deviceId: decodeURIComponent(parts[1]), deviceView: normalizeDeviceView(parts[0]) };
                }
                const tab = allowed.includes(parts[0]) ? parts[0] : 'devices';
                if (tab === 'devices' && parts[1] === 'swarm') {
                    if (parts[3] === 'swarm') {
                        const swarmViews = ['drones', 'downloads', 'master-list', 'gameplay'];
                        return { tab, swarmId: decodeURIComponent(parts[2]), deviceId: null, deviceView: 'systems', swarmView: swarmViews.includes(parts[4]) ? parts[4] : 'drones' };
                    }
                    if (parts[3] === 'device') {
                        return { tab, swarmId: decodeURIComponent(parts[2]), deviceId: parts[4] ? decodeURIComponent(parts[4]) : null, deviceView: normalizeDeviceView(parts[5]), swarmView: 'drones' };
                    }
                    const swarmViews = ['drones', 'downloads', 'master-list', 'gameplay'];
                    return { tab, deviceId: null, deviceView: 'systems', swarmView: swarmViews.includes(parts[2]) ? parts[2] : 'drones' };
                }
                if (tab === 'devices' && parts[1] === 'device') {
                    return { tab, deviceId: parts[2] ? decodeURIComponent(parts[2]) : null, deviceView: normalizeDeviceView(parts[3]), swarmView: 'drones' };
                }
                const deviceId = tab === 'devices' && parts[1] ? decodeURIComponent(parts[1]) : null;
                const deviceView = tab === 'devices' ? normalizeDeviceView(parts[2]) : 'systems';
                return { tab, deviceId, deviceView, swarmView: 'drones' };
            }

            function applyRouteFromHash() {
                const route = parseRoute();
                hydrateNavStateFromHash();
                routeSwarmId = route.swarmId || null;
                if (route.tab === 'devices' && route.swarmId && currentSwarms.some(s => s.id === route.swarmId)) {
                    selectedSwarmId = route.swarmId;
                    localStorage.setItem('selected_swarm_id', selectedSwarmId);
                }
                if (route.tab === 'devices' && !route.deviceId) {
                    selectedDeviceId = null;
                } else if (route.deviceId && currentDevices.some(d => d.device_id === route.deviceId)) {
                    selectedDeviceId = route.deviceId;
                    currentDeviceView = route.deviceView || 'overview';
                }
                switchTab(route.tab, null, false);
                if (selectedDeviceId && route.tab === 'devices') switchDeviceView(currentDeviceView, null, false);
                if (!selectedDeviceId && route.tab === 'devices') {
                    const view = route.swarmView || 'drones';
                    if (view === 'downloads') showSwarmDownloads(false);
                    else if (view === 'master-list') showSwarmMasterList(false);
                    else if (view === 'gameplay') showSwarmGameplay(false);
                    else showSwarmHome(false);
                }
                updateSharedSwarmNavButton();
                scrollAppToTop();
            }

            async function loadDeviceActions(options = {}) {
                const container = document.getElementById('actions-list');
                if (!selectedDeviceId || !container) return;
                const deviceId = selectedDeviceId;
                try {
                    const response = await apiGet(`/api/devices/${deviceId}/actions`, { showLoader: options.showLoader !== false });
                    if (!response.ok) throw new Error('Failed to load device actions');
                    const data = await response.json();
                    const actions = data.actions || [];
                    if (selectedDeviceId !== deviceId) return;
                    const signature = JSON.stringify(actions);
                    if (renderedDeviceActionsDeviceId === deviceId && renderedDeviceActionsSignature === signature && container.innerHTML.trim()) return;
                    const openResultIds = new Set(Array.from(container.querySelectorAll('details[data-action-result-id][open]'))
                        .map(details => details.dataset.actionResultId));
                    renderedDeviceActionsDeviceId = deviceId;
                    renderedDeviceActionsSignature = signature;
                    if (!actions.length) {
                        container.innerHTML = '<div class="empty-state">No actions queued yet.</div>';
                        return;
                    }
                    const statusBadge = {
                        pending: 'text-bg-secondary',
                        in_progress: 'text-bg-info',
                        completed: 'text-bg-success',
                        failed: 'text-bg-danger',
                    };
                    container.innerHTML = `
                        <div class="table-responsive">
                        <table class="table table-sm align-middle bff-stack">
                            <thead><tr>
                                <th>Action</th>
                                <th>Status</th>
                                <th>Created</th>
                                <th>Completed</th>
                                <th>Message</th>
                                <th>Result</th>
                            </tr></thead>
                            <tbody>${actions.map(action => {
                        const result = action.result || null;
                        const resultSummary = summarizeActionResult(result);
                        const mode = action.action === 'set_screen_mode' ? String((action.payload || {}).mode || '').toLowerCase() : '';
                        return `
                            <tr>
                                <td><strong>${formatActionName(action.action)}</strong>${mode ? `<div class="small text-muted">${escapeHtml(mode)}</div>` : ''}</td>
                                <td><span class="badge ${statusBadge[action.status] || 'text-bg-secondary'}">${escapeHtml(action.status || 'n/a')}</span></td>
                                <td class="small">${action.created_at ? new Date(action.created_at).toLocaleString() : 'n/a'}</td>
                                <td class="small">${action.completed_at ? new Date(action.completed_at).toLocaleString() : 'n/a'}</td>
                                <td class="small">${escapeHtml(action.message || '')}</td>
                                <td class="small">${result ? `
                                    <div class="text-muted">${escapeHtml(resultSummary)}</div>
                                    <details class="mt-1" data-action-result-id="${cssSafeId(action.id)}">
                                        <summary>View returned data</summary>
                                        <pre class="small mt-2 p-2 rounded" style="white-space:pre-wrap;background:rgba(0,0,0,0.18);max-height:360px;overflow:auto;">${escapeHtml(JSON.stringify(result, null, 2))}</pre>
                                    </details>
                                ` : 'n/a'}</td>
                            </tr>`;
                            }).join('')}</tbody>
                        </table>
                        </div>`;
                    container.querySelectorAll('details[data-action-result-id]').forEach(details => {
                        if (openResultIds.has(details.dataset.actionResultId)) details.open = true;
                    });
                } catch (error) {
                    console.error('Error loading actions:', error);
                    if (selectedDeviceId === deviceId && !container.innerHTML.trim()) {
                        container.innerHTML = '<div class="empty-state">Unable to load actions.</div>';
                    }
                }
            }

            async function pollDeviceActionResult(actionId, options = {}) {
                const intervalMs = options.intervalMs || 1200;
                const deadline = Date.now() + (options.timeoutMs || 20000);
                while (Date.now() < deadline) {
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/actions`, { showLoader: false });
                    if (response.ok) {
                        const data = await response.json();
                        const match = (data.actions || []).find(a => a.id === actionId);
                        if (match && match.status === 'completed') return match.result;
                        if (match && match.status === 'failed') throw new Error(match.message || 'Action failed on the Drone.');
                    }
                    await new Promise(resolve => setTimeout(resolve, intervalMs));
                }
                throw new Error('Timed out waiting for the Drone to respond.');
            }

            function renderEsCheckboxGrid(items, field) {
                if (!items.length) return '<div class="small text-muted">None found.</div>';
                return `<div class="row row-cols-2 row-cols-md-3 g-1">
                    ${items.map(item => `
                        <div class="col">
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" data-es-field="${field}" data-es-name="${escapeHtml(item.name)}" id="ov-es-${field}-${cssSafeId(item.name)}" ${item.checked ? 'checked' : ''}>
                                <label class="form-check-label small" for="ov-es-${field}-${cssSafeId(item.name)}">${escapeHtml(item.label)}</label>
                            </div>
                        </div>
                    `).join('')}
                </div>`;
            }

            function renderDeviceEsCollectionsCard(state) {
                const systems = state.systems || [];
                const groups = state.groups || [];
                const autoCollections = state.auto_collections || [];
                const customCollections = state.custom_collections || [];
                const groupsHtml = groups.length ? groups.map(group => `
                    <div class="mb-2">
                        <div class="small fw-semibold text-muted text-uppercase">${escapeHtml(group.group)}</div>
                        ${renderEsCheckboxGrid((group.children || []).map(c => ({name: c.name, label: c.full_name || c.name, checked: c.grouped})), 'grouped')}
                    </div>
                `).join('') : '<div class="small text-muted">No groupable systems found.</div>';
                return `
                    <div class="mb-2"><strong>Music Volume:</strong> ${Number.isFinite(Number(state.music_volume)) ? `${state.music_volume}%` : 'n/a'}</div>
                    <div class="mb-2"><strong>Screensaver:</strong> ${Number.isFinite(Number(state.screensaver_minutes)) ? (Number(state.screensaver_minutes) === 0 ? 'off' : `${state.screensaver_minutes} min`) : 'n/a'}</div>
                    <div class="mb-3">
                        <div class="fw-semibold mb-1">Systems Displayed</div>
                        ${renderEsCheckboxGrid(systems.map(s => ({name: s.name, label: s.full_name || s.name, checked: s.displayed})), 'displayed')}
                    </div>
                    <div class="mb-3">
                        <div class="fw-semibold mb-1">Grouped Systems</div>
                        <div class="small text-muted mb-2">Checked systems stay folded into their group's shared entry; uncheck to show a system standalone.</div>
                        ${groupsHtml}
                    </div>
                    <div class="mb-3">
                        <div class="fw-semibold mb-1">Automatic Game Collections</div>
                        ${renderEsCheckboxGrid(autoCollections.map(a => ({name: a.name, label: a.label || a.name, checked: a.enabled})), 'auto')}
                    </div>
                    <div class="mb-0">
                        <div class="fw-semibold mb-1">Custom Game Collections</div>
                        ${renderEsCheckboxGrid(customCollections.map(c => ({name: c.name, label: c.name, checked: c.enabled})), 'custom')}
                    </div>
                    <button class="btn btn-primary btn-sm mt-3" type="button" onclick="saveDeviceEsCollections()"><i class="bi bi-save me-1"></i>Save &amp; Restart EmulationStation</button>
                `;
            }

            function collectDeviceEsCollectionsPayload() {
                const container = document.getElementById('es-collections-body');
                if (!container) return {};
                const names = (field, wantChecked) => Array.from(container.querySelectorAll(`input[data-es-field="${field}"]`))
                    .filter(el => el.checked === wantChecked)
                    .map(el => el.dataset.esName);
                return {
                    hidden_systems: names('displayed', false),
                    ungrouped_systems: names('grouped', false),
                    auto_collections: names('auto', true),
                    custom_collections: names('custom', true),
                };
            }

            function renderDeviceEsCollectionsBody(state) {
                const container = document.getElementById('es-collections-body');
                if (!container) return;
                container.innerHTML = renderDeviceEsCollectionsCard(state);
            }

            async function loadDeviceEsCollections() {
                const container = document.getElementById('es-collections-body');
                if (!container || !selectedDeviceId) return;
                container.innerHTML = '<div class="text-muted small">Requesting current state from the Drone...</div>';
                try {
                    const queued = await queueDeviceAction('get_es_collections_state', { confirm: false, notify: false, refreshActions: false });
                    const action = queued && queued.action;
                    if (!action || !action.id) throw new Error('Action was not queued');
                    const result = await pollDeviceActionResult(action.id);
                    renderDeviceEsCollectionsBody(result || {});
                } catch (error) {
                    container.innerHTML = `<div class="empty-state">Unable to load collections: ${escapeHtml(error.message || 'unknown error')}. The Drone may be offline or slow to respond.</div>`;
                }
            }

            async function saveDeviceEsCollections() {
                if (!window.confirm('Save collections/systems changes and restart EmulationStation on this Drone now?')) return;
                const container = document.getElementById('es-collections-body');
                try {
                    const queued = await queueDeviceAction('set_es_collections', {
                        confirm: false,
                        notify: false,
                        payload: collectDeviceEsCollectionsPayload(),
                    });
                    const action = queued && queued.action;
                    if (!action || !action.id) throw new Error('Action was not queued');
                    if (container) container.innerHTML = '<div class="text-muted small">Applying changes on the Drone...</div>';
                    const result = await pollDeviceActionResult(action.id);
                    renderDeviceEsCollectionsBody(result || {});
                    showMessage('EmulationStation collections updated on the Drone.', 'success');
                } catch (error) {
                    showMessage(`Failed to update collections: ${error.message || 'unknown error'}`, 'danger');
                    if (container) await loadDeviceEsCollections();
                }
            }

            async function queueDeviceAction(actionName, options = {}) {
                if (!selectedDeviceId) return;
                const shouldConfirm = options.confirm !== false;
                const shouldRefreshActions = options.refreshActions !== false;
                const shouldNotify = options.notify !== false;
                const labels = {
                    restart: 'remote restart',
                    rebuild_asset_metadata: 'rebuild asset metadata',
                    run_pixen_update: 'run PixeN update',
                    refresh_emulator_list: 'refresh emulator list',
                    set_screen_mode: 'set screen mode',
                    set_volume: 'set volume',
                    set_music_volume: 'set music volume',
                    set_idle_volume_automation: 'update idle volume automation',
                    set_idle_game_exit_automation: 'update idle game exit automation',
                    set_wifi_recovery_automation: 'update Wi-Fi recovery automation',
                    collect_rom_metadata: 'collect ROM and system metadata',
                    collect_game_logs: 'collect Game Logs',
                    collect_emulator_configs: 'collect emulator configs',
                    collect_log_sources: 'collect log sources',
                };
                if (shouldConfirm && !window.confirm(`Queue ${labels[actionName] || actionName} for this Drone?`)) return;
                try {
                    const body = { action: actionName };
                    if (options.payload && typeof options.payload === 'object') body.payload = options.payload;
                    const response = await fetch(`/api/devices/${selectedDeviceId}/actions`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify(body)
                    });
                    if (response.status === 401) {
                        logout('Session expired. Please log in again.', '#/login');
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

            async function deleteDeviceActions() {
                if (!selectedDeviceId || !window.confirm('Delete all queued actions for this Drone?')) return;
                try {
                    const response = await apiDelete(`/api/devices/${selectedDeviceId}/actions`);
                    if (!response.ok) throw new Error('Failed to delete queued actions');
                    const data = await response.json();
                    await loadDeviceActions();
                    showMessage(`Deleted ${data.deleted_count || 0} queued action(s).`, 'success');
                } catch (error) {
                    console.error('Error deleting actions:', error);
                    showMessage('Failed to delete queued actions.', 'error');
                }
            }

            function formatActionName(actionName) {
                const labels = {
                    restart: 'Remote Restart',
                    refresh_emulator_list: 'Refresh Emulator List',
                    run_pixen_update: 'Run PixeN Update',
                    rebuild_asset_metadata: 'Rebuild Asset Metadata',
                    set_screen_mode: 'Set Screen Mode',
                    set_volume: 'Set Volume',
                    set_music_volume: 'Set Music Volume',
                    get_es_collections_state: 'Load Collections State',
                    set_es_collections: 'Update Collections',
                    set_idle_volume_automation: 'Idle Volume Automation',
                    set_idle_game_exit_automation: 'Idle Game Exit Automation',
                    set_wifi_recovery_automation: 'Wi-Fi Recovery Automation',
                    purge_asset_cache: 'Purge Asset Cache',
                    collect_game_logs: 'Game Logs',
                    collect_emulator_configs: 'Emulator Configs',
                    collect_log_sources: 'Log Sources',
                    collect_rom_metadata: 'ROM Metadata',
                };
                return labels[actionName] || String(actionName || 'n/a').replaceAll('_', ' ');
            }

            function summarizeActionResult(result) {
                if (!result) return '';
                if (result.type === 'asset_metadata_rebuild') return `${result.rom_count || 0} ROM entries, ${result.bios_count || 0} BIOS files, ${result.artwork_count || 0} artwork rows uploaded`;
                if (result.type === 'pixen_update') return result.status === 'started' ? `PixeN update started${result.pid ? ` (pid ${result.pid})` : ''}` : `PixeN update ${result.status || 'returned'}`;
                if (result.type === 'emulator_list_refresh') return result.emulationstation_restarted ? 'EmulationStation restart issued' : 'EmulationStation restart was not issued';
                if (result.type === 'screen_mode') return `Screen mode set to ${result.mode || 'unknown'}${result.emulationstation_restarted ? '; EmulationStation restarted' : ''}`;
                if (result.type === 'audio_volume') return result.muted ? 'Volume muted' : `Volume set to ${result.level}%`;
                if (result.type === 'es_collections_state') {
                    const autoOn = (result.auto_collections || []).filter(a => a.enabled).length;
                    const customOn = (result.custom_collections || []).filter(c => c.enabled).length;
                    const screensaver = Number.isFinite(Number(result.screensaver_minutes)) ? `${result.screensaver_minutes} min screensaver` : 'screensaver n/a';
                    return `Music volume ${Number.isFinite(Number(result.music_volume)) ? result.music_volume + '%' : 'n/a'}, ${screensaver}; ${(result.systems || []).length} systems, ${(result.groups || []).length} groups, ${autoOn} auto + ${customOn} custom collections enabled`;
                }
                if (result.type === 'idle_volume_automation') return result.enabled ? `Idle volume on: set to ${result.target_volume}% after ${result.idle_minutes} min idle` : 'Idle volume automation disabled';
                if (result.type === 'idle_game_exit_automation') return result.enabled ? `Idle game exit on: exit after ${result.idle_minutes} min idle` : 'Idle game exit automation disabled';
                if (result.type === 'wifi_recovery_automation') return result.enabled ? 'Wi-Fi recovery automation enabled' : 'Wi-Fi recovery automation disabled';
                if (result.type === 'rom_metadata') return `${(result.systems || []).length} systems, ${(result.roms || []).length} ROM entries, ${(result.gamelists || []).length} gamelist.xml files`;
                if (result.type === 'game_logs') return `${(result.sessions || []).length} parsed play sessions, ${(result.logs || []).length} logs`;
                if (result.type === 'emulator_configs') return `${(result.configs || []).length} config files`;
                if (result.type === 'log_sources') return `${(result.logs || []).length} log sources`;
                return 'Data returned from Drone';
            }

            function escapeHtml(value) {
                return String(value ?? '')
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
            }

            function jsAttr(value) {
                return escapeHtml(JSON.stringify(value));
            }

            function formatAdminDate(value) {
                return value ? new Date(value).toLocaleString() : 'n/a';
            }

            function formatBytes(value) {
                const num = Number(value);
                if (!Number.isFinite(num)) return 'n/a';
                const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
                let scaled = Math.max(0, num);
                let index = 0;
                while (scaled >= 1024 && index < units.length - 1) {
                    scaled /= 1024;
                    index += 1;
                }
                return `${scaled.toFixed(index ? 1 : 0)} ${units[index]}`;
            }

            function formatPercent(value) {
                const num = Number(value);
                return Number.isFinite(num) ? `${num.toFixed(1)}%` : 'n/a';
            }

            function renderMetricsGrid(metrics) {
                const cpu = metrics?.cpu || {};
                const memory = metrics?.memory || {};
                const process = metrics?.process || {};
                const disk = metrics?.disk || {};
                const rows = [
                    ['CPU host', formatPercent(cpu.host_percent)],
                    ['CPU app', formatPercent(cpu.process_percent)],
                    ['Load average', Array.isArray(cpu.load_average) ? cpu.load_average.map(v => Number(v).toFixed(2)).join(', ') : 'n/a'],
                    ['Memory', `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)} (${formatPercent(memory.used_percent)})`],
                    ['App RSS', formatBytes(process.rss_bytes)],
                    ['Disk used', `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.total_bytes)} (${formatPercent(disk.used_percent)})`],
                    ['Disk read', `${formatBytes(disk.read_bytes_per_second)}/s`],
                    ['Disk write', `${formatBytes(disk.write_bytes_per_second)}/s`],
                    ['Disk contention', formatPercent(disk.contention_percent)],
                    ['Updated', metrics?.collected_at ? new Date(metrics.collected_at).toLocaleString() : 'n/a'],
                ];
                return `<div class="row g-2">${rows.map(([label, value]) => `
                    <div class="col-12 col-md-6 col-xl-4">
                        <div class="small text-muted">${escapeHtml(label)}</div>
                        <div class="small fw-semibold">${escapeHtml(String(value || 'n/a'))}</div>
                    </div>
                `).join('')}</div>`;
            }

            async function loadSuperAdmin() {
                const summary = document.getElementById('super-admin-summary');
                const container = document.getElementById('super-admin-content');
                if (!summary || !container) return;
                if (!isSuperAdmin()) {
                    container.innerHTML = '<div class="empty-state">Super admin access required.</div>';
                    return;
                }
                container.innerHTML = '<div class="empty-state">Loading admin data...</div>';
                try {
                    const response = await apiGet('/api/admin/overview');
                    if (!response.ok) throw new Error('Failed to load admin data');
                    const data = await response.json();
                    const users = data.users || [];
                    const swarms = data.swarms || [];
                    const drones = data.drones || [];
                    const pendingConnections = data.pending_connections || [];
                    const swarmOptions = swarms.map(swarm => `
                        <option value="${escapeHtml(swarm.id)}">${escapeHtml(swarm.name || swarm.id)}${swarm.owner_email ? ` (${escapeHtml(swarm.owner_email)})` : ''}</option>
                    `).join('');
                    summary.innerHTML = `
                        <span class="badge text-bg-primary">Users: ${users.length}</span>
                        <span class="badge text-bg-primary">Swarms: ${swarms.length}</span>
                        <span class="badge text-bg-primary">Drones: ${drones.length}</span>
                        <span class="badge text-bg-primary">Pending: ${pendingConnections.length}</span>
                    `;
                    container.innerHTML = `
                        <div class="device-card mb-3">
                            <h4 class="h5">Pending Drone Connections</h4>
                            <div class="table-responsive"><table class="table table-sm align-middle bff-stack">
                                <thead><tr><th>Name</th><th>Drone ID</th><th>Reason</th><th>Network</th><th>Requested</th><th>Assign Swarm</th><th></th></tr></thead>
                                <tbody>${pendingConnections.map(conn => {
                                    const info = conn.batocera_info || {};
                                    const network = info.network || {};
                                    const ip = Array.isArray(network.ipv4) && network.ipv4.length ? network.ipv4[0] : (info.ip_address || '');
                                    const selectId = `admin-pending-swarm-${cssSafeId(conn.device_id)}`;
                                    return `
                                    <tr>
                                        <td>${escapeHtml(conn.device_name || 'Drone')}</td>
                                        <td class="mono">${escapeHtml(conn.device_id)}</td>
                                        <td><span class="badge text-bg-warning">${escapeHtml(conn.recovery_reason || 'pending')}</span></td>
                                        <td>${escapeHtml(info.reachable_url || ip || '')}</td>
                                        <td>${formatAdminDate(conn.last_seen || conn.detected_at)}</td>
                                        <td><select id="${selectId}" class="form-select form-select-sm">${swarmOptions || '<option value="">No swarms available</option>'}</select></td>
                                        <td class="text-end"><button class="btn btn-primary btn-sm" onclick="assignPendingDroneToSwarm(${JSON.stringify(conn.device_id)})" ${swarms.length ? '' : 'disabled'}>Assign</button></td>
                                    </tr>`;
                                }).join('') || '<tr><td colspan="7" class="text-muted">No pending Drone connections.</td></tr>'}</tbody>
                            </table></div>
                        </div>
                        <div class="device-card mb-3">
                            <h4 class="h5">Users</h4>
                            <div class="table-responsive"><table class="table table-sm align-middle bff-stack">
                                <thead><tr><th>Email</th><th>Name</th><th>Swarm</th><th>Provider</th><th>Status</th><th>Drones</th><th></th></tr></thead>
                                <tbody>${users.map(user => `
                                    <tr>
                                        <td>${escapeHtml(user.email)}</td>
                                        <td>${escapeHtml(user.full_name || user.username || '')}</td>
                                        <td>${escapeHtml(user.swarm_name || '')}</td>
                                        <td>${escapeHtml(user.auth_provider || 'password')}</td>
                                        <td>${user.is_active ? '<span class="badge text-bg-success">active</span>' : '<span class="badge text-bg-warning">inactive</span>'}</td>
                                        <td>${Number(user.drone_count || 0)}</td>
                                        <td class="text-end">${user.is_super_admin ? '<span class="badge text-bg-secondary">super admin</span>' : `<button class="btn btn-outline-danger btn-sm" onclick="deleteSuperAdminRecord('users', '${escapeHtml(user.id)}')">Delete</button>`}</td>
                                    </tr>`).join('') || '<tr><td colspan="7" class="text-muted">No users.</td></tr>'}</tbody>
                            </table></div>
                        </div>
                        <div class="device-card mb-3">
                            <h4 class="h5">Drones</h4>
                            <div class="table-responsive"><table class="table table-sm align-middle bff-stack">
                                <thead><tr><th>Name</th><th>Drone ID</th><th>Owner</th><th>Swarm</th><th>Status</th><th>Last Seen</th><th></th></tr></thead>
                                <tbody>${drones.map(drone => `
                                    <tr>
                                        <td>${escapeHtml(drone.device_name || 'Drone')}</td>
                                        <td class="mono">${escapeHtml(drone.device_id)}</td>
                                        <td>${escapeHtml(drone.owner_email || drone.user_id)}</td>
                                        <td>${escapeHtml(drone.swarm_name || drone.swarm_id || '')}</td>
                                        <td><span class="badge ${drone.approval_status === 'approved' ? 'text-bg-success' : 'text-bg-secondary'}">${escapeHtml(drone.approval_status || 'unknown')}</span></td>
                                        <td>${formatAdminDate(drone.last_seen)}</td>
                                        <td class="text-end"><button class="btn btn-outline-danger btn-sm" onclick="deleteSuperAdminRecord('drones', '${escapeHtml(drone.device_id)}')">Delete</button></td>
                                    </tr>`).join('') || '<tr><td colspan="7" class="text-muted">No drones.</td></tr>'}</tbody>
                            </table></div>
                        </div>
                        <div class="device-card">
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <h4 class="h5 mb-0">Sync Actions</h4>
                                <div class="d-flex flex-wrap align-items-center gap-2">
                                    <input id="sync-actions-search" class="form-control form-control-sm" type="text" placeholder="Search user / email / drone / system / rom" style="min-width: 260px;" onkeydown="if(event.key==='Enter'){event.preventDefault();searchSuperAdminSyncActions();}">
                                    <button class="btn btn-primary btn-sm" type="button" onclick="searchSuperAdminSyncActions()">Search</button>
                                    <button class="btn btn-outline-secondary btn-sm" type="button" onclick="clearSuperAdminSyncActionsSearch()">Clear</button>
                                    <select id="sync-actions-page-size" class="form-select form-select-sm" style="width: auto;" onchange="changeSyncActionsPageSize(this.value)" title="Rows per page">
                                        <option value="20">20</option>
                                        <option value="50">50</option>
                                        <option value="100">100</option>
                                    </select>
                                </div>
                            </div>
                            <div id="sync-actions-summary" class="d-flex flex-wrap gap-2 mb-2"></div>
                            <div id="sync-actions-results"><div class="empty-state">Loading sync actions…</div></div>
                        </div>
                        <div class="device-card mt-3">
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <h4 class="h5 mb-0">Audit Log</h4>
                                <div class="d-flex flex-wrap align-items-center gap-2">
                                    <input id="audit-log-search" class="form-control form-control-sm" type="text" placeholder="Search event / summary / email / target" style="min-width: 260px;" onkeydown="if(event.key==='Enter'){event.preventDefault();searchSuperAdminAuditLog();}">
                                    <button class="btn btn-primary btn-sm" type="button" onclick="searchSuperAdminAuditLog()">Search</button>
                                    <button class="btn btn-outline-secondary btn-sm" type="button" onclick="clearSuperAdminAuditLogSearch()">Clear</button>
                                    <select id="audit-log-page-size" class="form-select form-select-sm" style="width: auto;" onchange="changeAuditLogPageSize(this.value)" title="Rows per page">
                                        <option value="20">20</option>
                                        <option value="50">50</option>
                                        <option value="100">100</option>
                                    </select>
                                </div>
                            </div>
                            <div id="audit-log-results"><div class="empty-state">Loading audit log…</div></div>
                        </div>
                        <div class="device-card mt-3">
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <h4 class="h5 mb-0">Peer Transfers</h4>
                                <div class="d-flex flex-wrap align-items-center gap-2">
                                    <select id="transfers-status-filter" class="form-select form-select-sm" style="width: auto;" onchange="changeTransfersStatusFilter(this.value)" title="Filter by status" aria-label="Filter transfers by status">
                                        <option value="">All statuses</option>
                                        <option value="active">Active</option>
                                        <option value="completed">Completed</option>
                                        <option value="aborted">Aborted</option>
                                        <option value="offered">Offered</option>
                                        <option value="expired">Expired</option>
                                    </select>
                                    <select id="transfers-page-size" class="form-select form-select-sm" style="width: auto;" onchange="changeTransfersPageSize(this.value)" title="Rows per page" aria-label="Transfers per page">
                                        <option value="50">50</option>
                                        <option value="100">100</option>
                                        <option value="200">200</option>
                                    </select>
                                    <button class="btn btn-outline-secondary btn-sm" type="button" onclick="loadSuperAdminTransfers()" title="Refresh" aria-label="Refresh transfers"><i class="bi bi-arrow-clockwise"></i></button>
                                </div>
                            </div>
                            <div class="small text-muted mb-2">Drone-to-drone asset handoffs (LAN / direct / hole-punch / relay) coordinated by the control plane.</div>
                            <div id="transfers-results"><div class="empty-state">Loading transfers…</div></div>
                        </div>
                        <div class="device-card mt-3">
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <h4 class="h5 mb-0">Site Visitors</h4>
                                <span id="visitors-summary" class="d-flex flex-wrap gap-2"></span>
                            </div>
                            <div class="small text-muted mb-2">Unique IP addresses that loaded the landing page while signed out.</div>
                            <div id="visitors-results"><div class="empty-state">Loading visitors…</div></div>
                        </div>
                    `;
                    syncActionsOffset = 0;
                    auditLogOffset = 0;
                    visitorsOffset = 0;
                    transfersOffset = 0;
                    const pageSizeSelect = document.getElementById('sync-actions-page-size');
                    if (pageSizeSelect) pageSizeSelect.value = String(syncActionsPageSize);
                    const auditPageSizeSelect = document.getElementById('audit-log-page-size');
                    if (auditPageSizeSelect) auditPageSizeSelect.value = String(auditLogPageSize);
                    const transfersPageSizeSelect = document.getElementById('transfers-page-size');
                    if (transfersPageSizeSelect) transfersPageSizeSelect.value = String(transfersPageSize);
                    const transfersStatusSelect = document.getElementById('transfers-status-filter');
                    if (transfersStatusSelect) transfersStatusSelect.value = transfersStatusFilter;
                    loadSuperAdminSyncActions();
                    loadSuperAdminSyncSummary();
                    loadSuperAdminAuditLog();
                    loadSuperAdminTransfers();
                    loadSuperAdminVisitors();
                } catch (error) {
                    console.error('Error loading super admin data:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load super admin data.</div>';
                }
            }

            function syncActionStatusBadge(status) {
                const map = {
                    completed: 'text-bg-success',
                    failed: 'text-bg-danger',
                    error: 'text-bg-danger',
                    in_progress: 'text-bg-info',
                    claimed: 'text-bg-info',
                    pending: 'text-bg-secondary',
                    cancelled: 'text-bg-warning',
                    canceled: 'text-bg-warning',
                };
                return map[String(status || '').toLowerCase()] || 'text-bg-secondary';
            }

            function searchSuperAdminSyncActions() {
                syncActionsOffset = 0;
                loadSuperAdminSyncActions();
            }

            function clearSuperAdminSyncActionsSearch() {
                const input = document.getElementById('sync-actions-search');
                if (input) input.value = '';
                syncActionsOffset = 0;
                loadSuperAdminSyncActions();
            }

            function changeSyncActionsPageSize(value) {
                const size = Number(value) || 20;
                syncActionsPageSize = [20, 50, 100].includes(size) ? size : 20;
                syncActionsOffset = 0;
                loadSuperAdminSyncActions();
            }

            function gotoSyncActionsPage(offset) {
                syncActionsOffset = Math.max(0, Number(offset) || 0);
                loadSuperAdminSyncActions();
            }

            async function loadSuperAdminSyncActions() {
                const results = document.getElementById('sync-actions-results');
                if (!results || !isSuperAdmin()) return;
                const input = document.getElementById('sync-actions-search');
                const query = input ? input.value.trim() : '';
                const limit = syncActionsPageSize;
                const offset = syncActionsOffset;
                results.innerHTML = '<div class="empty-state">Loading sync actions…</div>';
                try {
                    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
                    if (query) params.set('q', query);
                    const response = await apiGet(`/api/admin/sync-actions?${params.toString()}`, { showLoader: false });
                    if (!response.ok) throw new Error('Failed to load sync actions');
                    const data = await response.json();
                    const actions = data.sync_actions || [];
                    const total = Number(data.total || 0);
                    const start = total ? offset + 1 : 0;
                    const end = offset + actions.length;
                    const hasPrev = offset > 0;
                    const hasNext = end < total;
                    results.innerHTML = `
                        <div class="table-responsive"><table class="table table-sm align-middle bff-stack">
                            <thead><tr><th>User</th><th>Email</th><th>Drone</th><th>System</th><th>ROM</th><th>Action</th><th>Status</th><th>Created</th></tr></thead>
                            <tbody>${actions.map(action => `
                                <tr>
                                    <td>${escapeHtml(action.full_name || action.username || '')}</td>
                                    <td>${escapeHtml(action.email || '')}</td>
                                    <td class="mono">${escapeHtml(action.device_name || action.device_id || '')}</td>
                                    <td>${escapeHtml(action.system || '')}</td>
                                    <td>${escapeHtml(action.rom || '')}</td>
                                    <td>${escapeHtml(action.action || '')}</td>
                                    <td><span class="badge ${syncActionStatusBadge(action.status)}">${escapeHtml(action.status || '')}</span></td>
                                    <td>${formatAdminDate(action.created_at)}</td>
                                </tr>`).join('') || `<tr><td colspan="8" class="text-muted">${query ? 'No sync actions match your search.' : 'No sync actions yet.'}</td></tr>`}</tbody>
                        </table></div>
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mt-2">
                            <span class="small text-muted">${total ? `Showing ${start}–${end} of ${total}` : 'No results'}</span>
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-secondary" type="button" ${hasPrev ? '' : 'disabled'} onclick="gotoSyncActionsPage(${Math.max(0, offset - limit)})">Previous</button>
                                <button class="btn btn-outline-secondary" type="button" ${hasNext ? '' : 'disabled'} onclick="gotoSyncActionsPage(${offset + limit})">Next</button>
                            </div>
                        </div>`;
                } catch (error) {
                    console.error('Error loading sync actions:', error);
                    results.innerHTML = '<div class="empty-state">Unable to load sync actions.</div>';
                }
            }

            async function loadSuperAdminSyncSummary() {
                const container = document.getElementById('sync-actions-summary');
                if (!container || !isSuperAdmin()) return;
                try {
                    const response = await apiGet('/api/admin/sync-actions/summary', { showLoader: false });
                    if (!response.ok) throw new Error('Failed to load sync summary');
                    const data = await response.json();
                    const byStatus = data.by_status || {};
                    const order = ['completed', 'in_progress', 'pending', 'failed', 'cancelled'];
                    const keys = order.filter(k => k in byStatus).concat(Object.keys(byStatus).filter(k => !order.includes(k)));
                    container.innerHTML = `
                        <span class="badge text-bg-primary">Total: ${Number(data.total || 0)}</span>
                        ${keys.map(key => `<span class="badge ${syncActionStatusBadge(key)}">${escapeHtml(key.replace('_', ' '))}: ${Number(byStatus[key] || 0)}</span>`).join('')}
                    `;
                } catch (error) {
                    console.error('Error loading sync summary:', error);
                    container.innerHTML = '';
                }
            }

            function searchSuperAdminAuditLog() {
                auditLogOffset = 0;
                loadSuperAdminAuditLog();
            }

            function clearSuperAdminAuditLogSearch() {
                const input = document.getElementById('audit-log-search');
                if (input) input.value = '';
                auditLogOffset = 0;
                loadSuperAdminAuditLog();
            }

            function changeAuditLogPageSize(value) {
                const size = Number(value) || 20;
                auditLogPageSize = [20, 50, 100].includes(size) ? size : 20;
                auditLogOffset = 0;
                loadSuperAdminAuditLog();
            }

            function gotoAuditLogPage(offset) {
                auditLogOffset = Math.max(0, Number(offset) || 0);
                loadSuperAdminAuditLog();
            }

            async function loadSuperAdminAuditLog() {
                const results = document.getElementById('audit-log-results');
                if (!results || !isSuperAdmin()) return;
                const input = document.getElementById('audit-log-search');
                const query = input ? input.value.trim() : '';
                const limit = auditLogPageSize;
                const offset = auditLogOffset;
                results.innerHTML = '<div class="empty-state">Loading audit log…</div>';
                try {
                    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
                    if (query) params.set('q', query);
                    const response = await apiGet(`/api/admin/audit-log?${params.toString()}`, { showLoader: false });
                    if (!response.ok) throw new Error('Failed to load audit log');
                    const data = await response.json();
                    const events = data.audit_events || [];
                    const total = Number(data.total || 0);
                    const start = total ? offset + 1 : 0;
                    const end = offset + events.length;
                    const hasPrev = offset > 0;
                    const hasNext = end < total;
                    results.innerHTML = `
                        <div class="table-responsive"><table class="table table-sm align-middle bff-stack">
                            <thead><tr><th>When</th><th>Event</th><th>Summary</th><th>Actor</th><th>Target</th></tr></thead>
                            <tbody>${events.map(event => `
                                <tr>
                                    <td>${formatAdminDate(event.created_at)}</td>
                                    <td><span class="badge text-bg-secondary">${escapeHtml(String(event.event_type || '').replace(/_/g, ' '))}</span></td>
                                    <td>${escapeHtml(event.summary || '')}</td>
                                    <td>${escapeHtml(event.actor_email || '')}</td>
                                    <td>${escapeHtml(event.target_label || event.target_id || '')}</td>
                                </tr>`).join('') || `<tr><td colspan="5" class="text-muted">${query ? 'No audit events match your search.' : 'No audit events yet.'}</td></tr>`}</tbody>
                        </table></div>
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mt-2">
                            <span class="small text-muted">${total ? `Showing ${start}–${end} of ${total}` : 'No results'}</span>
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-secondary" type="button" ${hasPrev ? '' : 'disabled'} onclick="gotoAuditLogPage(${Math.max(0, offset - limit)})">Previous</button>
                                <button class="btn btn-outline-secondary" type="button" ${hasNext ? '' : 'disabled'} onclick="gotoAuditLogPage(${offset + limit})">Next</button>
                            </div>
                        </div>`;
                } catch (error) {
                    console.error('Error loading audit log:', error);
                    results.innerHTML = '<div class="empty-state">Unable to load audit log.</div>';
                }
            }

            function transferStatusBadge(status) {
                const map = {
                    completed: 'text-bg-success',
                    active: 'text-bg-info',
                    offered: 'text-bg-secondary',
                    aborted: 'text-bg-danger',
                    expired: 'text-bg-warning',
                };
                return map[String(status || '').toLowerCase()] || 'text-bg-secondary';
            }

            function transferTransportBadge(transport) {
                const map = {
                    lan: 'text-bg-success',
                    'direct-public': 'text-bg-primary',
                    holepunch: 'text-bg-info',
                    relay: 'text-bg-warning',
                };
                return map[String(transport || '').toLowerCase()] || 'text-bg-secondary';
            }

            function changeTransfersStatusFilter(value) {
                transfersStatusFilter = String(value || '');
                transfersOffset = 0;
                loadSuperAdminTransfers();
            }

            function changeTransfersPageSize(value) {
                const size = Number(value) || 50;
                transfersPageSize = [50, 100, 200].includes(size) ? size : 50;
                transfersOffset = 0;
                loadSuperAdminTransfers();
            }

            function gotoTransfersPage(offset) {
                transfersOffset = Math.max(0, Number(offset) || 0);
                loadSuperAdminTransfers();
            }

            async function loadSuperAdminTransfers() {
                const results = document.getElementById('transfers-results');
                if (!results || !isSuperAdmin()) return;
                const limit = transfersPageSize;
                const offset = transfersOffset;
                results.innerHTML = '<div class="empty-state">Loading transfers…</div>';
                try {
                    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
                    if (transfersStatusFilter) params.set('status', transfersStatusFilter);
                    const response = await apiGet(`/api/admin/transfers?${params.toString()}`, { showLoader: false });
                    if (!response.ok) throw new Error('Failed to load transfers');
                    const data = await response.json();
                    const transfers = data.transfers || [];
                    const total = Number(data.total || 0);
                    const start = total ? offset + 1 : 0;
                    const end = offset + transfers.length;
                    const hasPrev = offset > 0;
                    const hasNext = end < total;
                    results.innerHTML = `
                        <div class="table-responsive"><table class="table table-sm align-middle bff-stack">
                            <thead><tr><th>When</th><th>From → To</th><th>Asset</th><th>Transport</th><th class="d-none d-lg-table-cell">Bytes</th><th>Status</th></tr></thead>
                            <tbody>${transfers.map(t => {
                                const asset = t.asset || {};
                                const assetLabel = [asset.kind, asset.system, asset.relative_path].filter(Boolean).join(' / ');
                                const when = t.updated_at_epoch || t.created_at_epoch;
                                const bytesLabel = t.bytes_total ? `${formatBytes(t.bytes_done || 0)} / ${formatBytes(t.bytes_total)}` : (t.bytes_done ? formatBytes(t.bytes_done) : '');
                                return `
                                <tr>
                                    <td class="small text-muted">${escapeHtml(when ? formatAdminDate(when * 1000) : '')}</td>
                                    <td class="small mono">${escapeHtml(t.from_device || '')} &rarr; ${escapeHtml(t.to_device || '')}</td>
                                    <td class="small">${escapeHtml(assetLabel)}</td>
                                    <td>${t.transport_used ? `<span class="badge ${transferTransportBadge(t.transport_used)}">${escapeHtml(t.transport_used)}</span>` : '<span class="text-muted small">—</span>'}</td>
                                    <td class="small d-none d-lg-table-cell">${escapeHtml(bytesLabel)}</td>
                                    <td><span class="badge ${transferStatusBadge(t.status)}">${escapeHtml(t.status || '')}</span>${t.error ? `<div class="small text-danger">${escapeHtml(t.error)}</div>` : ''}</td>
                                </tr>`;
                            }).join('') || `<tr><td colspan="6" class="text-muted">${transfersStatusFilter ? 'No transfers match this filter.' : 'No transfers yet.'}</td></tr>`}</tbody>
                        </table></div>
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mt-2">
                            <span class="small text-muted">${total ? `Showing ${start}–${end} of ${total}` : 'No results'}</span>
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-secondary" type="button" ${hasPrev ? '' : 'disabled'} onclick="gotoTransfersPage(${Math.max(0, offset - limit)})">Previous</button>
                                <button class="btn btn-outline-secondary" type="button" ${hasNext ? '' : 'disabled'} onclick="gotoTransfersPage(${offset + limit})">Next</button>
                            </div>
                        </div>`;
                } catch (error) {
                    console.error('Error loading transfers:', error);
                    results.innerHTML = '<div class="empty-state">Unable to load transfers.</div>';
                }
            }

            function gotoVisitorsPage(offset) {
                visitorsOffset = Math.max(0, Number(offset) || 0);
                loadSuperAdminVisitors();
            }

            async function loadSuperAdminVisitors() {
                const results = document.getElementById('visitors-results');
                const summaryEl = document.getElementById('visitors-summary');
                if (!results || !isSuperAdmin()) return;
                const limit = visitorsPageSize;
                const offset = visitorsOffset;
                results.innerHTML = '<div class="empty-state">Loading visitors…</div>';
                try {
                    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
                    const response = await apiGet(`/api/admin/landing-visits?${params.toString()}`, { showLoader: false });
                    if (!response.ok) throw new Error('Failed to load visitors');
                    const data = await response.json();
                    const visits = data.visits || [];
                    const totalRows = Number(data.total_rows || 0);
                    if (summaryEl) summaryEl.innerHTML = `
                        <span class="badge text-bg-primary">Unique IPs: ${Number(data.unique || 0)}</span>
                        <span class="badge text-bg-secondary">Total visits: ${Number(data.total || 0)}</span>`;
                    const start = totalRows ? offset + 1 : 0;
                    const end = offset + visits.length;
                    const hasPrev = offset > 0;
                    const hasNext = end < totalRows;
                    results.innerHTML = `
                        <div class="table-responsive"><table class="table table-sm align-middle bff-stack">
                            <thead><tr><th>IP Address</th><th>Visits</th><th>First Seen</th><th>Last Seen</th></tr></thead>
                            <tbody>${visits.map(visit => `
                                <tr>
                                    <td class="mono">${escapeHtml(visit.ip || '')}</td>
                                    <td>${Number(visit.visit_count || 0)}</td>
                                    <td>${formatAdminDate(visit.first_seen)}</td>
                                    <td>${formatAdminDate(visit.last_seen)}</td>
                                </tr>`).join('') || '<tr><td colspan="4" class="text-muted">No visitors recorded yet.</td></tr>'}</tbody>
                        </table></div>
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mt-2">
                            <span class="small text-muted">${totalRows ? `Showing ${start}–${end} of ${totalRows}` : 'No results'}</span>
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-secondary" type="button" ${hasPrev ? '' : 'disabled'} onclick="gotoVisitorsPage(${Math.max(0, offset - limit)})">Previous</button>
                                <button class="btn btn-outline-secondary" type="button" ${hasNext ? '' : 'disabled'} onclick="gotoVisitorsPage(${offset + limit})">Next</button>
                            </div>
                        </div>`;
                } catch (error) {
                    console.error('Error loading visitors:', error);
                    results.innerHTML = '<div class="empty-state">Unable to load visitors.</div>';
                }
            }

            async function assignPendingDroneToSwarm(deviceId) {
                if (!isSuperAdmin()) return;
                const select = document.getElementById(`admin-pending-swarm-${cssSafeId(deviceId)}`);
                const swarmId = select ? select.value : '';
                if (!swarmId) {
                    showMessage('Choose a swarm first.', 'error');
                    return;
                }
                try {
                    const response = await apiPost(`/api/admin/drone-connections/${encodeURIComponent(deviceId)}/assign`, { swarm_id: swarmId });
                    if (!response.ok) throw new Error('Failed to assign Drone');
                    await loadSuperAdmin();
                    showMessage('Drone assigned to swarm.', 'success');
                } catch (error) {
                    console.error('Error assigning pending Drone:', error);
                    showMessage(error.message || 'Assignment failed.', 'error');
                }
            }

            async function loadSuperAdminMetrics() {
                const container = document.getElementById('super-admin-metrics');
                if (!container || !isSuperAdmin()) return;
                try {
                    const response = await apiGet('/api/admin/runtime-metrics', { showLoader: false });
                    if (!response.ok) throw new Error('Failed to load runtime metrics');
                    const payload = await response.json();
                    container.innerHTML = `
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                            <h4 class="h5 mb-0">Overmind Runtime Metrics</h4>
                            <span class="badge text-bg-secondary">refreshes every 5s</span>
                        </div>
                        ${renderMetricsGrid(payload.metrics || {})}
                    `;
                } catch (error) {
                    console.error('Error loading runtime metrics:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load runtime metrics.</div>';
                }
            }

            function newestLogLinesFirst(text, limit = null) {
                const lines = String(text ?? '').split(/\r?\n/);
                if (lines.length && !lines[lines.length - 1]) lines.pop();
                const selected = Number.isInteger(limit) && limit > 0 ? lines.slice(-limit) : lines;
                return selected.reverse().join('\n');
            }

            // Newest log lines are at the top. Keep the pane pinned there while the
            // user is reading fresh output, otherwise preserve their scroll offset.
            function updateLogPane(pre, text) {
                if (!pre) return;
                const wasEmpty = !pre.textContent;
                const wasAtTop = wasEmpty || pre.scrollTop < 32;
                const previousScrollTop = pre.scrollTop;
                if (pre.textContent !== text) {
                    pre.textContent = text;
                }
                pre.scrollTop = wasAtTop ? 0 : previousScrollTop;
            }

            function ensureSuperAdminLogShell(container) {
                if (!container || document.getElementById('super-admin-log-stdout')) return;
                container.innerHTML = `
                    <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                        <h4 class="h5 mb-0">Overmind Runtime Logs</h4>
                        <div class="d-flex flex-wrap align-items-center gap-2">
                            <span class="badge text-bg-secondary" id="super-admin-log-maxlines"></span>
                            <span class="small text-muted" id="super-admin-log-status" aria-live="polite"></span>
                        </div>
                    </div>
                    <div class="runtime-log-section mb-3">
                        <div class="runtime-log-header">
                            <span class="small text-muted">stdout</span>
                            <button class="btn btn-outline-secondary btn-sm" type="button" title="Copy stdout logs" onclick="copyRuntimeLogPane('super-admin-log-stdout', 'stdout')"><i class="bi bi-clipboard me-1"></i>Copy</button>
                        </div>
                        <pre id="super-admin-log-stdout" class="runtime-log-pane mono" tabindex="0"></pre>
                    </div>
                    <div class="runtime-log-section">
                        <div class="runtime-log-header">
                            <span class="small text-muted">stderr</span>
                            <button class="btn btn-outline-secondary btn-sm" type="button" title="Copy stderr logs" onclick="copyRuntimeLogPane('super-admin-log-stderr', 'stderr')"><i class="bi bi-clipboard me-1"></i>Copy</button>
                        </div>
                        <pre id="super-admin-log-stderr" class="runtime-log-pane mono" tabindex="0"></pre>
                    </div>
                    <div class="small text-muted mt-2" id="super-admin-log-captured"></div>
                `;
            }

            async function copyRuntimeLogPane(elementId, label) {
                const pane = document.getElementById(elementId);
                const text = pane ? pane.textContent || '' : '';
                try {
                    await copyTextToClipboard(text);
                    showMessage(`${label} logs copied.`, 'success');
                } catch (error) {
                    console.error('Runtime log copy failed:', error);
                    showMessage(error.message || 'Copy failed', 'error');
                }
            }

            async function loadSuperAdminLogs() {
                const container = document.getElementById('super-admin-logs');
                if (!container || !isSuperAdmin()) return;
                ensureSuperAdminLogShell(container);
                try {
                    const response = await apiGet('/api/admin/runtime-logs', { showLoader: false });
                    if (!response.ok) throw new Error('Failed to load runtime logs');
                    const payload = await response.json();
                    const logs = payload.logs || {};
                    const maxLinesBadge = document.getElementById('super-admin-log-maxlines');
                    if (maxLinesBadge) maxLinesBadge.textContent = `last ${Number(logs.max_lines || 0)} lines`;
                    updateLogPane(document.getElementById('super-admin-log-stdout'), newestLogLinesFirst(logs.stdout || 'No stdout captured yet.'));
                    updateLogPane(document.getElementById('super-admin-log-stderr'), newestLogLinesFirst(logs.stderr || 'No stderr captured yet.'));
                    const capturedEl = document.getElementById('super-admin-log-captured');
                    if (capturedEl) capturedEl.textContent = `Captured: ${logs.captured_at ? new Date(logs.captured_at).toLocaleString() : 'n/a'}`;
                    const statusEl = document.getElementById('super-admin-log-status');
                    if (statusEl) statusEl.textContent = '';
                } catch (error) {
                    console.error('Error loading runtime logs:', error);
                    const statusEl = document.getElementById('super-admin-log-status');
                    if (statusEl) statusEl.textContent = 'Update failed; showing last captured logs.';
                }
            }

            function startSuperAdminMetricsPolling() {
                if (!isSuperAdmin()) return;
                loadSuperAdminMetrics();
                loadSuperAdminLogs();
                if (superAdminMetricsTimer) return;
                superAdminMetricsTimer = setInterval(() => {
                    if (currentTab === 'super-admin') {
                        loadSuperAdminMetrics();
                        loadSuperAdminLogs();
                    }
                }, 5000);
            }

            function stopSuperAdminMetricsPolling() {
                if (superAdminMetricsTimer) {
                    clearInterval(superAdminMetricsTimer);
                    superAdminMetricsTimer = null;
                }
            }

            async function deleteSuperAdminRecord(kind, id) {
                if (!isSuperAdmin()) return;
                if (!window.confirm(`Delete this ${kind.slice(0, -1)}? This cannot be undone.`)) return;
                try {
                    const response = await apiDelete(`/api/admin/${kind}/${encodeURIComponent(id)}`);
                    if (!response.ok) throw new Error(`Failed to delete ${kind}`);
                    await loadSuperAdmin();
                    showMessage('Deleted.', 'success');
                } catch (error) {
                    console.error('Error deleting admin record:', error);
                    showMessage(error.message || 'Delete failed.', 'error');
                }
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
                    currentDeviceView = 'overview';
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
                if (updateUrl) {
                    setRoute(tabName);
                    return;
                }
                activateNav(tabName);
                document.querySelectorAll('.dashboard-tab').forEach(section => { section.style.display = 'none'; });
                const tabMap = {
                    devices: 'devices-tab',
                    hive: 'hive-tab',
                    profile: 'profile-tab',
                    notifications: 'notifications-tab',
                    'super-admin': 'super-admin-tab',
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
                if (tabName === 'notifications') {
                    markNotificationsRead();
                    loadNotificationsPage();
                }
                if (tabName === 'super-admin') {
                    loadSuperAdmin();
                    startSuperAdminMetricsPolling();
                } else {
                    stopSuperAdminMetricsPolling();
                }
                updateSelectedDeviceSummary();
                applyRbacUI();
                setPageChrome(tabName);
                updateSharedSwarmNavButton();
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
                if (!text) throw new Error('No text available to copy.');
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

            function toggleElement(elementId) {
                const element = document.getElementById(elementId);
                if (!element) return;
                element.style.display = element.style.display === 'none' || !element.style.display ? 'block' : 'none';
            }

            function showMessage(message, type) {
                showToast(message, type, type === 'error' || type === 'danger' ? 8000 : 5000);
            }
