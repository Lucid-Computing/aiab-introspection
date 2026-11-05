/* Install after all trusted imports, before receiving evidence. Linux x86_64.
 * TSYNC covers V8/libuv threads. Only existing stdio is retained by the parent.
 * Fail closed; Node is not run inside the legacy Python worker. */
#include <node_api.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <linux/audit.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <errno.h>
#define DENY(n) BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,n,0,1), BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM)
static napi_value install(napi_env env, napi_callback_info info) {
  struct sock_filter rules[] = {
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,4),
    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AUDIT_ARCH_X86_64,1,0),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
    BPF_STMT(BPF_LD|BPF_W|BPF_ABS,0),
    BPF_JUMP(BPF_JMP|BPF_JGE|BPF_K,0x40000000,0,1),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
    DENY(__NR_open), DENY(__NR_openat), DENY(__NR_openat2),
    DENY(__NR_stat), DENY(__NR_lstat), DENY(__NR_newfstatat), DENY(__NR_statx),
    DENY(__NR_access), DENY(__NR_faccessat), DENY(__NR_faccessat2),
    DENY(__NR_readlink), DENY(__NR_readlinkat), DENY(__NR_getdents), DENY(__NR_getdents64),
    DENY(__NR_getcwd), DENY(__NR_chdir), DENY(__NR_fchdir),
    DENY(__NR_creat), DENY(__NR_socket), DENY(__NR_socketpair),
    DENY(__NR_connect), DENY(__NR_bind), DENY(__NR_listen),
    DENY(__NR_accept), DENY(__NR_accept4),
    DENY(__NR_execve), DENY(__NR_execveat),
    DENY(__NR_fork), DENY(__NR_vfork), DENY(__NR_clone), DENY(__NR_clone3),
    DENY(__NR_ptrace), DENY(__NR_process_vm_readv), DENY(__NR_process_vm_writev),
    DENY(__NR_kill), DENY(__NR_tkill), DENY(__NR_tgkill),
    DENY(__NR_unlink), DENY(__NR_unlinkat), DENY(__NR_rename), DENY(__NR_renameat),
    DENY(__NR_renameat2), DENY(__NR_mkdir), DENY(__NR_mkdirat),
    DENY(__NR_link), DENY(__NR_linkat), DENY(__NR_symlink), DENY(__NR_symlinkat),
    DENY(__NR_mount), DENY(__NR_umount2), DENY(__NR_unshare), DENY(__NR_setns),
    DENY(__NR_io_uring_setup), DENY(__NR_bpf), DENY(__NR_perf_event_open),
    DENY(__NR_chmod), DENY(__NR_fchmod), DENY(__NR_fchmodat),
    DENY(__NR_truncate), DENY(__NR_ftruncate),
    DENY(__NR_memfd_create), DENY(__NR_userfaultfd),
    BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW)
  };
  struct sock_fprog p = {sizeof(rules)/sizeof(rules[0]),rules};
  if (prctl(PR_SET_DUMPABLE,0) || prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0) ||
      syscall(__NR_seccomp,SECCOMP_SET_MODE_FILTER,SECCOMP_FILTER_FLAG_TSYNC,&p)) {
    napi_throw_error(env,NULL,"sandbox_unavailable"); return NULL;
  }
  napi_value v; napi_get_boolean(env,1,&v); return v;
}
static napi_value init(napi_env env, napi_value exports) {
  napi_value fn; napi_create_function(env,"install",NAPI_AUTO_LENGTH,install,NULL,&fn);
  napi_set_named_property(env,exports,"install",fn); return exports;
}
NAPI_MODULE(NODE_GYP_MODULE_NAME,init)
