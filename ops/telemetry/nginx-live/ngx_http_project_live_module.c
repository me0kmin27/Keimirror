/* Linux/nginx 1.26.3 exact-build module. Private, aggregate-only ABI. */
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>
#include <ngx_event.h>
#include <ngx_process_cycle.h>
#include <sys/random.h>

#define LIVE_REPOS 14
#define LIVE_WORKERS NGX_MAX_PROCESSES
static const char *live_names[LIVE_REPOS] = {
    "archlinux", "artix", "artix-iso", "artix-weekly-iso", "cachyos",
    "endeavouros", "fyralabs", "gentoo", "gentoo-portage", "kali",
    "kali-images", "manjaro", "opnsense", "proxmox"
};
typedef struct {
    uint64_t rx_bytes, tx_bytes, uncertain, active_uncertain;
} live_counter;
typedef struct {
    ngx_pid_t pid;
    ngx_msec_t started, heartbeat;
    ngx_uint_t ready;
} live_worker;
typedef struct {
    u_char epoch[32];
    live_counter repos[LIVE_REPOS];
    live_worker workers[LIVE_WORKERS];
} live_shared;
typedef struct { ngx_shm_zone_t *zone; } live_main;
typedef struct { ngx_flag_t enabled; } live_server;

extern ngx_module_t ngx_http_project_live_module;
static live_shared *live;
static ngx_slab_pool_t *live_pool;
static ngx_event_t live_timer;
typedef struct live_request_s live_request;
typedef struct {
    ngx_rbtree_node_t node;
    ngx_connection_t *connection;
    ngx_send_chain_pt send_chain;
    ngx_send_pt send;
    live_request *request;
    ngx_uint_t nesting;
} live_connection;
struct live_request_s {
    live_connection *connection;
    ngx_http_request_t *request;
    ngx_uint_t repo, uncertain;
};
static ngx_rbtree_t live_connections;
static ngx_rbtree_node_t live_sentinel;
static ngx_http_output_header_filter_pt live_next_header;
static ngx_int_t live_read_request(ngx_http_request_t *r);
static ngx_int_t live_header(ngx_http_request_t *r);
static ngx_chain_t *live_send_chain(ngx_connection_t *c, ngx_chain_t *in, off_t limit);
static ssize_t live_send(ngx_connection_t *c, u_char *buf, size_t size);
static ngx_int_t live_postconf(ngx_conf_t *cf);
static ngx_int_t live_worker_init(ngx_cycle_t *cycle);
static void live_worker_exit(ngx_cycle_t *cycle);
static ngx_int_t live_status(ngx_http_request_t *r);

static void *live_create_main(ngx_conf_t *cf) {
    return ngx_pcalloc(cf->pool, sizeof(live_main));
}
static void *live_create_server(ngx_conf_t *cf) {
    live_server *s = ngx_pcalloc(cf->pool, sizeof(*s));
    if (s) s->enabled = NGX_CONF_UNSET;
    return s;
}
static char *live_merge_server(ngx_conf_t *cf, void *parent, void *child) {
    live_server *p = parent, *c = child;
    ngx_conf_merge_value(c->enabled, p->enabled, 0);
    return NGX_CONF_OK;
}
static ngx_int_t live_zone_init(ngx_shm_zone_t *zone, void *old) {
    u_char random[16];
    ngx_slab_pool_t *pool = (ngx_slab_pool_t *) zone->shm.addr;
    live_shared *s;
    if (getrandom(random, sizeof(random), 0) != sizeof(random)) return NGX_ERROR;
    s = ngx_slab_calloc(pool, sizeof(*s));
    if (s == NULL) return NGX_ERROR;
    ngx_hex_dump(s->epoch, random, sizeof(random));
    zone->data = s;
    return NGX_OK;
}
static char *live_zone_conf(ngx_conf_t *cf, ngx_command_t *cmd, void *conf) {
    live_main *m = conf;
    ngx_str_t name = ngx_string("project_live_v1");
    if (m->zone) return "is duplicate";
    m->zone = ngx_shared_memory_add(cf, &name, 256 * 1024,
                                    &ngx_http_project_live_module);
    if (!m->zone) return NGX_CONF_ERROR;
    m->zone->init = live_zone_init;
    /* Never mix draining workers from a previous configuration generation. */
    m->zone->noreuse = 1;
    return NGX_CONF_OK;
}
static char *live_status_conf(ngx_conf_t *cf, ngx_command_t *cmd, void *conf) {
    ngx_http_core_loc_conf_t *l;
    l = ngx_http_conf_get_module_loc_conf(cf, ngx_http_core_module);
    l->handler = live_status;
    return NGX_CONF_OK;
}
static ngx_command_t live_commands[] = {
    { ngx_string("project_live_zone"), NGX_HTTP_MAIN_CONF|NGX_CONF_NOARGS,
      live_zone_conf, NGX_HTTP_MAIN_CONF_OFFSET, 0, NULL },
    { ngx_string("project_live"), NGX_HTTP_SRV_CONF|NGX_CONF_FLAG,
      ngx_conf_set_flag_slot, NGX_HTTP_SRV_CONF_OFFSET,
      offsetof(live_server, enabled), NULL },
    { ngx_string("project_live_status"), NGX_HTTP_LOC_CONF|NGX_CONF_NOARGS,
      live_status_conf, NGX_HTTP_LOC_CONF_OFFSET, 0, NULL },
    ngx_null_command
};
static ngx_http_module_t live_context = {
    NULL, live_postconf, live_create_main, NULL,
    live_create_server, live_merge_server, NULL, NULL
};
ngx_module_t ngx_http_project_live_module = {
    NGX_MODULE_V1, &live_context, live_commands, NGX_HTTP_MODULE,
    NULL, NULL, live_worker_init, NULL, NULL, live_worker_exit, NULL,
    NGX_MODULE_V1_PADDING
};
static void live_tick(ngx_event_t *ev) {
    live_worker *w = &live->workers[ngx_process_slot];
    ngx_shmtx_lock(&live_pool->mutex);
    w->heartbeat = ngx_current_msec;
    w->ready = !ngx_exiting;
    ngx_shmtx_unlock(&live_pool->mutex);
    if (!ngx_exiting) ngx_add_timer(ev, 1000);
}
static ngx_int_t live_worker_init(ngx_cycle_t *cycle) {
    live_main *m = ngx_http_cycle_get_module_main_conf(cycle, ngx_http_project_live_module);
    live_worker *w;
    if (!m || !m->zone) return NGX_OK;
    if (ngx_process != NGX_PROCESS_WORKER && ngx_process != NGX_PROCESS_SINGLE)
        return NGX_OK;
    if (ngx_process_slot < 0 || ngx_process_slot >= LIVE_WORKERS) return NGX_ERROR;
    live = m->zone->data;
    ngx_rbtree_init(&live_connections, &live_sentinel, ngx_rbtree_insert_value);
    live_pool = (ngx_slab_pool_t *) m->zone->shm.addr;
    w = &live->workers[ngx_process_slot];
    ngx_shmtx_lock(&live_pool->mutex);
    w->pid = ngx_pid;
    w->started = w->heartbeat = ngx_current_msec;
    w->ready = 1;
    ngx_shmtx_unlock(&live_pool->mutex);
    ngx_memzero(&live_timer, sizeof(live_timer));
    live_timer.handler = live_tick;
    live_timer.log = cycle->log;
    live_timer.cancelable = 1;
    ngx_add_timer(&live_timer, 1000);
    return NGX_OK;
}
static void live_worker_exit(ngx_cycle_t *cycle) {
    if (!live) return;
    ngx_shmtx_lock(&live_pool->mutex);
    live->workers[ngx_process_slot].ready = 0;
    ngx_shmtx_unlock(&live_pool->mutex);
}
static ngx_int_t live_status(ngx_http_request_t *r) {
    ngx_buf_t *b;
    ngx_chain_t out;
    u_char *p;
    ngx_uint_t i, comma;
    ngx_int_t rc;
    live_server *s = ngx_http_get_module_srv_conf(r, ngx_http_project_live_module);
    if (ngx_connection_local_sockaddr(r->connection, NULL, 0) != NGX_OK
        || r->connection->local_sockaddr->sa_family != AF_UNIX || s->enabled)
        return NGX_HTTP_FORBIDDEN;
    if (r->method != NGX_HTTP_GET) return NGX_HTTP_NOT_ALLOWED;
    if (!live) return NGX_HTTP_SERVICE_UNAVAILABLE;
    b = ngx_create_temp_buf(r->pool, 256 * 1024);
    if (!b) return NGX_HTTP_INTERNAL_SERVER_ERROR;
    p = b->last;
    ngx_shmtx_lock(&live_pool->mutex);
    p = ngx_sprintf(p, "{\"schema_version\":1,\"epoch\":\"%*s\","
                      "\"workers\":[", (size_t)32, live->epoch);
    comma = 0;
    for (i = 0; i < LIVE_WORKERS; i++) {
        if (!live->workers[i].ready
            || (ngx_msec_int_t)(ngx_current_msec - live->workers[i].heartbeat) > 2000)
            continue;
        p = ngx_sprintf(p, "%s%P", comma++ ? "," : "", live->workers[i].pid);
    }
    p = ngx_sprintf(p, "],\"repositories\":{");
    for (i = 0; i < LIVE_REPOS; i++) {
        live_counter *c = &live->repos[i];
        p = ngx_sprintf(p, "%s\"%s\":{\"rx_bytes\":%uL,\"tx_bytes\":%uL,"
                          "\"uncertain\":%uL,\"active_uncertain\":%uL}",
                        i ? "," : "", live_names[i], c->rx_bytes, c->tx_bytes,
                        c->uncertain, c->active_uncertain);
    }
    p = ngx_sprintf(p, "}}\n");
    ngx_shmtx_unlock(&live_pool->mutex);
    b->last = p;
    b->last_buf = 1;
    r->headers_out.status = NGX_HTTP_OK;
    r->headers_out.content_length_n = p - b->pos;
    ngx_str_set(&r->headers_out.content_type, "application/json");
    rc = ngx_http_send_header(r);
    if (rc == NGX_ERROR || rc > NGX_OK || r->header_only) return rc;
    out.buf = b; out.next = NULL;
    return ngx_http_output_filter(r, &out);
}
/* No c->data ownership: HTTP replaces it during keepalive and request setup. */
static live_connection *live_lookup(ngx_connection_t *c) {
    ngx_rbtree_node_t *n = live_connections.root;
    ngx_rbtree_key_t key = (ngx_rbtree_key_t) (uintptr_t)c;
    while (n != live_connections.sentinel) {
        if (n->key == key) return (live_connection *)n;
        n = key < n->key ? n->left : n->right;
    }
    return NULL;
}
static void live_fault(void) {
    ngx_uint_t i;
    /* Allocation/association failure cannot become an apparently healthy zero. */
    ngx_shmtx_lock(&live_pool->mutex);
    for (i = 0; i < LIVE_REPOS; i++) {
        live->repos[i].uncertain++;
        live->repos[i].active_uncertain++;
    }
    ngx_shmtx_unlock(&live_pool->mutex);
}
static void live_uncertain(live_request *q) {
    if (q->uncertain) return;
    q->uncertain = 1;
    ngx_shmtx_lock(&live_pool->mutex);
    live->repos[q->repo].uncertain++;
    live->repos[q->repo].active_uncertain++;
    ngx_shmtx_unlock(&live_pool->mutex);
}
static void live_request_done(void *data) {
    live_request *q = data;
    if (q->request->connection->error || q->request->connection->timedout)
        live_uncertain(q);
    if (q->uncertain) {
        ngx_shmtx_lock(&live_pool->mutex);
        live->repos[q->repo].active_uncertain--;
        ngx_shmtx_unlock(&live_pool->mutex);
    }
    if (q->connection && q->connection->request == q)
        q->connection->request = NULL;
}
static void live_connection_done(void *data) {
    live_connection *lc = data;
    ngx_rbtree_delete(&live_connections, &lc->node);
}
static ngx_int_t live_repo(ngx_http_request_t *r) {
    size_t n;
    ngx_uint_t i;
    if (r->uri.len < 2 || r->uri.data[0] != '/') return NGX_DECLINED;
    for (n = 1; n < r->uri.len && r->uri.data[n] != '/'; n++) { /* segment */ }
    for (i = 0; i < LIVE_REPOS; i++) {
        if (ngx_strlen(live_names[i]) == n - 1
            && ngx_strncmp(r->uri.data + 1, live_names[i], n - 1) == 0)
            return (ngx_int_t)i;
    }
    return NGX_DECLINED;
}
static live_request *live_bind(ngx_http_request_t *r, ngx_uint_t early) {
    live_server *s;
    live_connection *lc;
    live_request *q;
    ngx_pool_cleanup_t *cl;
    ngx_int_t repo;
    ngx_uint_t protocol_ok, rx_ok;
    if (!live || r != r->main) return NULL;
    s = ngx_http_get_module_srv_conf(r, ngx_http_project_live_module);
    if (!s->enabled) return NULL;
    protocol_ok = r->http_version == NGX_HTTP_VERSION_10
                  || r->http_version == NGX_HTTP_VERSION_11;
    lc = protocol_ok ? live_lookup(r->connection) : NULL;
    if (lc && lc->request && lc->request->request == r) return lc->request;
    q = ngx_http_get_module_ctx(r, ngx_http_project_live_module);
    if (q) return q;
    repo = live_repo(r);
    if (repo < 0) return NULL;
    cl = ngx_pool_cleanup_add(r->pool, sizeof(live_request));
    if (!cl) { live_fault(); return NULL; }
    q = cl->data;
    ngx_memzero(q, sizeof(*q));
    q->repo = (ngx_uint_t)repo;
    q->request = r;
    cl->handler = live_request_done;
    ngx_http_set_ctx(r, q, ngx_http_project_live_module);
    if (protocol_ok) {
        if (!lc) {
            cl = ngx_pool_cleanup_add(r->connection->pool, sizeof(live_connection));
            if (!cl) { live_uncertain(q); return q; }
            lc = cl->data;
            ngx_memzero(lc, sizeof(*lc));
            lc->node.key = (ngx_rbtree_key_t)(uintptr_t)r->connection;
            lc->connection = r->connection;
            lc->send_chain = r->connection->send_chain;
            lc->send = r->connection->send;
            ngx_rbtree_insert(&live_connections, &lc->node);
            cl->handler = live_connection_done;
            r->connection->send_chain = live_send_chain;
            r->connection->send = live_send;
        }
        if (lc->request) {
            live_uncertain(lc->request);
            live_uncertain(q);
        }
        lc->request = q;
        q->connection = lc;
    }
    /* POST_READ precedes body/discard processing; never sample request_length
     * in a body filter (it can include a future pipelined request there). */
    rx_ok = early && protocol_ok && !r->pipeline
        && (r->method == NGX_HTTP_GET || r->method == NGX_HTTP_HEAD)
        /* Upgraded tunnel reads bypass HTTP request-length accounting. */
        && !r->headers_in.upgrade
        && !r->headers_in.chunked && r->headers_in.content_length_n <= 0
        && r->header_in->pos == r->header_in->last;
    if (rx_ok) {
        ngx_shmtx_lock(&live_pool->mutex);
        live->repos[q->repo].rx_bytes += (uint64_t)r->request_length;
        ngx_shmtx_unlock(&live_pool->mutex);
    } else live_uncertain(q);
    return q;
}
static void live_progress(live_connection *lc, off_t before) {
    live_request *q = lc->request;
    off_t delta = lc->connection->sent - before;
    if (!q) return;
    if (delta < 0) { live_uncertain(q); return; }
    if (delta) {
        ngx_shmtx_lock(&live_pool->mutex);
        live->repos[q->repo].tx_bytes += (uint64_t)delta;
        ngx_shmtx_unlock(&live_pool->mutex);
    }
}
static ngx_chain_t *live_send_chain(ngx_connection_t *c, ngx_chain_t *in, off_t limit) {
    live_connection *lc = live_lookup(c);
    ngx_chain_t *out;
    off_t before = c->sent;
    if (!lc) { live_fault(); return NGX_CHAIN_ERROR; }
    lc->nesting++;
    out = lc->send_chain(c, in, limit);
    if (--lc->nesting == 0) live_progress(lc, before);
    /* Count c->sent advancement even when the final result is an error. */
    if (out == NGX_CHAIN_ERROR && lc->request) live_uncertain(lc->request);
    return out;
}
static ssize_t live_send(ngx_connection_t *c, u_char *buf, size_t size) {
    live_connection *lc = live_lookup(c);
    ssize_t n;
    off_t before = c->sent;
    if (!lc) { live_fault(); return NGX_ERROR; }
    lc->nesting++;
    n = lc->send(c, buf, size);
    if (--lc->nesting == 0) live_progress(lc, before);
    if (n == NGX_ERROR && lc->request) live_uncertain(lc->request);
    return n;
}
static ngx_int_t live_read_request(ngx_http_request_t *r) {
    (void)live_bind(r, 1);
    return NGX_DECLINED;
}
static ngx_int_t live_header(ngx_http_request_t *r) {
    live_request *q = live_bind(r, 0);
    if (q && (r->headers_out.status == NGX_HTTP_SWITCHING_PROTOCOLS
              || r->headers_out.status >= NGX_HTTP_BAD_REQUEST || r->internal))
        live_uncertain(q);
    return live_next_header(r);
}
static ngx_int_t live_postconf(ngx_conf_t *cf) {
    ngx_http_core_main_conf_t *cmcf;
    ngx_http_handler_pt *h;
    cmcf = ngx_http_conf_get_module_main_conf(cf, ngx_http_core_module);
    h = ngx_array_push(&cmcf->phases[NGX_HTTP_POST_READ_PHASE].handlers);
    if (!h) return NGX_ERROR;
    *h = live_read_request;
    live_next_header = ngx_http_top_header_filter;
    ngx_http_top_header_filter = live_header;
    return NGX_OK;
}
