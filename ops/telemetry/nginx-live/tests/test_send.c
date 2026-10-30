/* Real wrapper functions, deterministic send returns; integration covers nginx. */
#include "../ngx_http_project_live_module.c"
#include <assert.h>
#include <stdio.h>
void ngx_shmtx_lock(ngx_shmtx_t *m) { (void)m; }
void ngx_shmtx_unlock(ngx_shmtx_t *m) { (void)m; }
static int mode;
static ssize_t send_stub(ngx_connection_t *c, u_char *b, size_t n) {
    (void)b; (void)n;
    c->sent += 7;
    return 7;
}
static ngx_chain_t *chain_stub(ngx_connection_t *c, ngx_chain_t *in, off_t limit) {
    (void)limit;
    if (mode == 0) { c->sent += 13; return NGX_CHAIN_ERROR; }
    if (mode == 1) { c->send(c, NULL, 7); c->sent += 5; return in; }
    return in; /* EAGAIN/no progress, not requested chain length */
}
static void test_error_eligibility(void) {
    ngx_connection_t c = {0}, other_c = {0};
    ngx_http_request_t r = {0}, other_r = {0};
    live_connection lc = {0};
    live_request q = {0};
    r.main = &r; r.connection = &c;
    ngx_str_set(&r.uri, "/archlinux/missing");
    r.headers_out.status = r.err_status = NGX_HTTP_NOT_FOUND;
    q.request = &r; q.connection = &lc;
    lc.connection = &c; lc.request = &q;
    lc.send = send_stub; lc.send_chain = chain_stub;
    c.send = live_send; c.send_chain = live_send_chain;
    assert(live_accounted_error(&r, &q));
    /* Each removed proof must reject independently; no status-only bypass. */
    q.uncertain = 1; assert(!live_accounted_error(&r, &q)); q.uncertain = 0;
    r.internal = 1; assert(!live_accounted_error(&r, &q)); r.internal = 0;
    r.error_page = 1; assert(!live_accounted_error(&r, &q)); r.error_page = 0;
    r.upstream = (ngx_http_upstream_t *)&other_r;
    assert(!live_accounted_error(&r, &q)); r.upstream = NULL;
    r.err_status = 0; assert(!live_accounted_error(&r, &q)); r.err_status = 404;
    r.headers_out.status = r.err_status = 413;
    assert(!live_accounted_error(&r, &q));
    r.headers_out.status = r.err_status = 404;
    r.main = &other_r; assert(!live_accounted_error(&r, &q)); r.main = &r;
    q.request = &other_r; assert(!live_accounted_error(&r, &q)); q.request = &r;
    q.repo = 1; assert(!live_accounted_error(&r, &q)); q.repo = 0;
    q.connection = NULL; assert(!live_accounted_error(&r, &q)); q.connection = &lc;
    lc.connection = &other_c; assert(!live_accounted_error(&r, &q)); lc.connection = &c;
    lc.request = NULL; assert(!live_accounted_error(&r, &q)); lc.request = &q;
    lc.send = NULL; assert(!live_accounted_error(&r, &q)); lc.send = send_stub;
    lc.send_chain = NULL; assert(!live_accounted_error(&r, &q)); lc.send_chain = chain_stub;
    c.send = send_stub; assert(!live_accounted_error(&r, &q)); c.send = live_send;
    c.send_chain = chain_stub; assert(!live_accounted_error(&r, &q));
    c.send_chain = live_send_chain;
    assert(live_accounted_error(&r, &q));
}
int main(void) {
    test_error_eligibility();
    live_shared shared = {0};
    ngx_slab_pool_t pool = {0};
    ngx_connection_t c = {0};
    live_connection lc = {0};
    live_request q = {0};
    ngx_chain_t in = {0};
    live = &shared; live_pool = &pool;
    ngx_rbtree_init(&live_connections, &live_sentinel, ngx_rbtree_insert_value);
    lc.node.key = (uintptr_t)&c; lc.connection = &c; lc.request = &q;
    lc.send_chain = chain_stub; lc.send = send_stub; q.connection = &lc;
    ngx_rbtree_insert(&live_connections, &lc.node);
    c.send = live_send; c.send_chain = live_send_chain;
    assert(c.send_chain(&c, &in, 100000) == NGX_CHAIN_ERROR);
    assert(shared.repos[0].tx_bytes == 13);
    assert(shared.repos[0].uncertain == 1 && shared.repos[0].active_uncertain == 1);
    mode = 1;
    assert(c.send_chain(&c, &in, 100000) == &in);
    assert(shared.repos[0].tx_bytes == 25); /* nested send not double counted */
    mode = 2;
    assert(c.send_chain(&c, &in, 100000) == &in);
    assert(shared.repos[0].tx_bytes == 25);
    puts("PASS partial-error progress, nested send guard, EAGAIN zero");
    return 0;
}
