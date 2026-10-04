package com.mistycloud.accounthelper;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.accessibilityservice.GestureDescription;
import android.app.ActivityManager;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Gravity;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Button;
import android.widget.Toast;

import java.util.ArrayDeque;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MistyOverlayService extends AccessibilityService {
    private static final String TAG = "MistyOverlay";
    private static final String TARGET_PACKAGE = "com.mistycloud.android";

    private WindowManager windowManager;
    private Button switchButton;
    private boolean overlayVisible;
    private long lastDumpAt;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private boolean busy;
    private String pendingEmail = "";
    private String pendingPassword = "";
    private int loginStage;
    private long stageStartedAt;
    private int actionRetries;

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();

        AccessibilityServiceInfo info = getServiceInfo();
        info.eventTypes =
                AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                        | AccessibilityEvent.TYPE_WINDOWS_CHANGED
                        | AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED;
        info.feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC;
        info.notificationTimeout = 100;
        info.flags |= AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS;
        info.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS;
        info.flags |= AccessibilityServiceInfo.FLAG_REQUEST_ENHANCED_WEB_ACCESSIBILITY;
        setServiceInfo(info);

        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        Log.i(TAG, "service connected");
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;

        String packageName = resolveActivePackage(event);
        Log.i(TAG, "event type=" + event.getEventType() + " pkg=" + packageName);

        if (getPackageName().equals(packageName)) return;

        if (TARGET_PACKAGE.equals(packageName)) {
            showOverlay();
            long now = SystemClock.elapsedRealtime();
            if (now - lastDumpAt > 1500) {
                lastDumpAt = now;
                dumpTree("event:" + event.getEventType());
            }
        } else if ((event.getEventType() == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                || event.getEventType() == AccessibilityEvent.TYPE_WINDOWS_CHANGED)
                && !"com.android.systemui".equals(packageName)
                && !packageName.contains("inputmethod")
                && !packageName.contains("keyboard")) {
            hideOverlay();
        }
    }

    private String resolveActivePackage(AccessibilityEvent event) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root != null && root.getPackageName() != null) {
            return root.getPackageName().toString();
        }
        CharSequence fromEvent = event.getPackageName();
        return fromEvent == null ? "" : fromEvent.toString();
    }

    private void showOverlay() {
        if (overlayVisible || windowManager == null) return;

        switchButton = new Button(this);
        switchButton.setText("换号");
        switchButton.setTextSize(15);
        switchButton.setTextColor(Color.WHITE);
        switchButton.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        switchButton.setAllCaps(false);
        switchButton.setPadding(dp(14), dp(8), dp(14), dp(8));

        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.rgb(20, 20, 20));
        bg.setCornerRadius(dp(18));
        switchButton.setBackground(bg);

        switchButton.setOnClickListener(v -> startSwitch());

        WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                dp(78),
                dp(48),
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT);
        params.gravity = Gravity.END | Gravity.CENTER_VERTICAL;
        params.x = dp(10);
        params.y = dp(20);

        try {
            windowManager.addView(switchButton, params);
            overlayVisible = true;
            Log.i(TAG, "overlay shown");
        } catch (RuntimeException error) {
            Log.e(TAG, "show overlay failed", error);
        }
    }

    private void hideOverlay() {
        if (!overlayVisible || switchButton == null || windowManager == null) return;
        try {
            windowManager.removeView(switchButton);
        } catch (RuntimeException ignored) {
        }
        switchButton = null;
        overlayVisible = false;
        Log.i(TAG, "overlay hidden");
    }

    private void startSwitch() {
        if (busy) return;
        busy = true;
        setButtonText("注册中");
        worker.execute(() -> {
            try {
                RegistrationStore store = new RegistrationStore(this);
                RegistrationStore.State state = store.load();
                RegistrationService registration = new RegistrationService();
                RegistrationService.Mailbox mailbox;

                if (!state.pendingEmail.isEmpty()) {
                    mailbox = new RegistrationService.Mailbox(
                            state.pendingEmail, state.pendingPassword);
                    if (!registration.canLogin(mailbox)) {
                        registration.register(mailbox, this::progress);
                    }
                } else {
                    mailbox = registration.createMailbox();
                    state.pendingEmail = mailbox.email;
                    state.pendingPassword = mailbox.password;
                    store.save(state);
                    registration.register(mailbox, this::progress);
                }

                if (!registration.canLogin(mailbox)) {
                    throw new RegistrationService.Failure("新账号登录校验失败");
                }

                state.lastEmail = mailbox.email;
                state.lastPassword = mailbox.password;
                state.registeredAtMs = System.currentTimeMillis();
                state.pendingEmail = "";
                state.pendingPassword = "";
                store.save(state);

                pendingEmail = mailbox.email;
                pendingPassword = mailbox.password;
                main.post(this::beginUiSwitch);
            } catch (Exception error) {
                main.post(() -> fail("失败：" + safeMessage(error)));
            }
        });
    }

    private void progress(String message) {
        main.post(() -> setButtonText(shortStatus(message)));
    }

    private void beginUiSwitch() {
        setButtonText("登出");
        if (!clickDesc("menu")) {
            tapRatio(0.075f, 0.085f);
        }
        actionRetries = 0;
        main.postDelayed(this::clickLogoutStep, 450L);
    }

    private void clickLogoutStep() {
        if (!busy) return;
        if (clickText("登出", "退出登录", "退出登陆", "Log out")) {
            actionRetries = 0;
            setButtonText("确认");
            main.postDelayed(this::confirmLogoutStep, 350L);
            return;
        }
        if (actionRetries == 2 || actionRetries == 6 || actionRetries == 10) {
            if (!clickDesc("menu")) {
                tapRatio(0.075f, 0.085f);
            }
        }
        if (actionRetries++ < 14) {
            main.postDelayed(this::clickLogoutStep, 300L);
        } else {
            fail("找不到 Misty 登出按钮");
        }
    }

    private void confirmLogoutStep() {
        if (!busy) return;
        if (clickText("确定", "確定", "確認", "确认", "OK")) {
            loginStage = 1;
            stageStartedAt = System.currentTimeMillis();
            setButtonText("填账号");
            main.postDelayed(this::pumpLogin, 550L);
            return;
        }
        if (actionRetries++ < 14) {
            main.postDelayed(this::confirmLogoutStep, 300L);
        } else {
            fail("找不到登出确认按钮");
        }
    }

    private void pumpLogin() {
        if (!busy || loginStage < 1 || loginStage > 3) return;
        long elapsed = System.currentTimeMillis() - stageStartedAt;
        long timeout = loginStage == 3 ? 20000L : (loginStage == 2 ? 18000L : 12000L);
        if (elapsed > timeout) {
            fail(loginStage == 1 ? "找不到邮箱输入框" : "登录页面超时");
            return;
        }

        AccessibilityNodeInfo field = findEditable(getRootInActiveWindow());
        if (loginStage == 1) {
            if (field == null) {
                main.postDelayed(this::pumpLogin, 350L);
                return;
            }
            CharSequence hint = field.getHintText();
            String h = hint == null ? "" : hint.toString();
            if (!h.isEmpty() && (h.contains("密码") || h.contains("密碼")
                    || h.toLowerCase().contains("password")
                    || h.toLowerCase().contains("tell anyone"))) {
                main.postDelayed(this::pumpLogin, 350L);
                return;
            }
            if (!setText(field, pendingEmail)) {
                fail("邮箱填入失败");
                return;
            }
            setButtonText("下一步");
            pressEnterOrNext(field);
            loginStage = 2;
            stageStartedAt = System.currentTimeMillis();
            main.postDelayed(this::pumpLogin, 900L);
            return;
        }

        if (loginStage == 2) {
            if (field == null) {
                main.postDelayed(this::pumpLogin, 350L);
                return;
            }
            CharSequence hint = field.getHintText();
            String h = hint == null ? "" : hint.toString();
            String currentValue = field.getText() == null ? "" : field.getText().toString();
            if (h.contains("邮箱") || h.contains("郵箱") || h.toLowerCase().contains("email")
                    || currentValue.contains("@")) {
                main.postDelayed(this::pumpLogin, 350L);
                return;
            }
            if (!setText(field, pendingPassword)) {
                fail("密码填入失败");
                return;
            }
            setButtonText("登录");
            pressEnterOrNext(field);
            loginStage = 3;
            stageStartedAt = System.currentTimeMillis();
            main.postDelayed(this::pumpLogin, 2500L);
            return;
        }

        if (findDesc("menu") != null) {
            restartMisty();
            return;
        }
        main.postDelayed(this::pumpLogin, 500L);
    }

    private AccessibilityNodeInfo findEditable(AccessibilityNodeInfo root) {
        if (root == null) return null;
        ArrayDeque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        queue.add(root);
        int visited = 0;
        while (!queue.isEmpty() && visited++ < 250) {
            AccessibilityNodeInfo node = queue.removeFirst();
            CharSequence className = node.getClassName();
            if (node.isEditable() || "android.widget.EditText".contentEquals(className)) {
                return node;
            }
            for (int i = 0; i < node.getChildCount(); i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) queue.addLast(child);
            }
        }
        return null;
    }

    private boolean clickText(String... labels) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return false;
        for (String label : labels) {
            java.util.List<AccessibilityNodeInfo> nodes =
                    root.findAccessibilityNodeInfosByText(label);
            if (nodes == null) continue;
            for (AccessibilityNodeInfo node : nodes) {
                AccessibilityNodeInfo target = node;
                int hops = 0;
                while (target != null && !target.isClickable() && hops++ < 6) {
                    target = target.getParent();
                }
                if (target != null && target.isClickable()
                        && target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                    Log.i(TAG, "clicked text=" + label);
                    return true;
                }
                android.graphics.Rect bounds = new android.graphics.Rect();
                node.getBoundsInScreen(bounds);
                if (!bounds.isEmpty()) {
                    Log.i(TAG, "gesture text=" + label + " bounds=" + bounds);
                    tapPoint(bounds.centerX(), bounds.centerY());
                    return true;
                }
            }
        }
        return false;
    }

    private AccessibilityNodeInfo findDesc(String value) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return null;
        ArrayDeque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        queue.add(root);
        int visited = 0;
        while (!queue.isEmpty() && visited++ < 450) {
            AccessibilityNodeInfo node = queue.removeFirst();
            CharSequence desc = node.getContentDescription();
            if (desc != null && value.equalsIgnoreCase(desc.toString().trim())) {
                return node;
            }
            for (int i = 0; i < node.getChildCount(); i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) queue.addLast(child);
            }
        }
        return null;
    }

    private boolean clickDesc(String value) {
        AccessibilityNodeInfo node = findDesc(value);
        if (node == null) return false;
        AccessibilityNodeInfo target = node;
        int hops = 0;
        while (target != null && !target.isClickable() && hops++ < 6) {
            target = target.getParent();
        }
        if (target != null && target.isClickable()
                && target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            Log.i(TAG, "clicked desc=" + value);
            return true;
        }
        android.graphics.Rect bounds = new android.graphics.Rect();
        node.getBoundsInScreen(bounds);
        if (bounds.isEmpty()) return false;
        Log.i(TAG, "gesture desc=" + value + " bounds=" + bounds);
        tapPoint(bounds.centerX(), bounds.centerY());
        return true;
    }

    private boolean setText(AccessibilityNodeInfo node, String value) {
        try {
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
            Bundle args = new Bundle();
            args.putCharSequence(
                    AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
            return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        } catch (RuntimeException error) {
            Log.e(TAG, "set text failed", error);
            return false;
        }
    }

    private void pressEnterOrNext(AccessibilityNodeInfo node) {
        boolean handled = false;
        if (Build.VERSION.SDK_INT >= 30) {
            try {
                handled = node.performAction(
                        AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId());
            } catch (RuntimeException ignored) {
            }
        }
        if (!handled) {
            main.postDelayed(() -> {
                if (!clickText("arrow_right_circle_fill")
                        && !clickDesc("arrow_right_circle_fill")) {
                    tapRatio(0.88f, 0.41f);
                }
            }, 250L);
        }
    }

    private void restartMisty() {
        loginStage = 4;
        setButtonText("重启");
        performGlobalAction(GLOBAL_ACTION_HOME);
        main.postDelayed(() -> {
            try {
                ActivityManager manager =
                        (ActivityManager) getSystemService(ACTIVITY_SERVICE);
                if (manager != null) manager.killBackgroundProcesses(TARGET_PACKAGE);
            } catch (RuntimeException ignored) {
            }
            main.postDelayed(() -> {
                try {
                    Intent launch = getPackageManager()
                            .getLaunchIntentForPackage(TARGET_PACKAGE);
                    if (launch == null) {
                        throw new IllegalStateException("找不到 Misty 启动入口");
                    }
                    launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                            | Intent.FLAG_ACTIVITY_CLEAR_TASK);
                    startActivity(launch);
                    main.postDelayed(() -> {
                        busy = false;
                        loginStage = 0;
                        pendingEmail = "";
                        pendingPassword = "";
                        setButtonText("换号");
                        Toast.makeText(this, "Misty 换号完成", Toast.LENGTH_SHORT).show();
                    }, 1300L);
                } catch (Exception error) {
                    fail("重启 Misty 失败");
                }
            }, 500L);
        }, 600L);
    }

    private void tapRatio(float xRatio, float yRatio) {
        int width = getResources().getDisplayMetrics().widthPixels;
        int height = getResources().getDisplayMetrics().heightPixels;
        tapPoint(width * xRatio, height * yRatio);
    }

    private void tapPoint(float x, float y) {
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, 70))
                .build();
        dispatchGesture(gesture, null, null);
    }

    private void setButtonText(String text) {
        if (switchButton != null) switchButton.setText(text);
    }

    private void fail(String message) {
        busy = false;
        loginStage = 0;
        setButtonText("重试");
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
        main.postDelayed(() -> setButtonText("换号"), 2500L);
    }

    private static String safeMessage(Exception error) {
        String message = error.getMessage();
        return message == null || message.trim().isEmpty() ? "未知错误" : message;
    }

    private static String shortStatus(String message) {
        if (message == null) return "处理中";
        if (message.contains("验证码")) return "等验证码";
        if (message.contains("注册")) return "注册中";
        return "处理中";
    }

    private void dumpTree(String reason) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) {
            Log.i(TAG, "tree " + reason + ": root=null");
            return;
        }
        Log.i(TAG, "tree " + reason + " begin");
        dumpNode(root, 0, 0);
        Log.i(TAG, "tree " + reason + " end");
    }

    private int dumpNode(AccessibilityNodeInfo node, int depth, int count) {
        if (node == null || depth > 10 || count > 180) return count;

        CharSequence text = node.getText();
        CharSequence desc = node.getContentDescription();
        String viewId = node.getViewIdResourceName();

        Log.i(TAG,
                "node depth=" + depth
                        + " class=" + node.getClassName()
                        + " text=" + safe(text)
                        + " desc=" + safe(desc)
                        + " id=" + (viewId == null ? "" : viewId)
                        + " clickable=" + node.isClickable()
                        + " editable=" + node.isEditable());

        int next = count + 1;
        for (int i = 0; i < node.getChildCount() && next <= 180; i++) {
            next = dumpNode(node.getChild(i), depth + 1, next);
        }
        return next;
    }

    private static String safe(CharSequence value) {
        if (value == null) return "";
        String s = value.toString().replace('\n', ' ').replace('\r', ' ');
        return s.length() > 80 ? s.substring(0, 80) : s;
    }

    private int dp(int value) {
        return Math.round(getResources().getDisplayMetrics().density * value);
    }

    @Override
    public void onInterrupt() {
        hideOverlay();
    }

    @Override
    public void onDestroy() {
        hideOverlay();
        main.removeCallbacksAndMessages(null);
        worker.shutdownNow();
        super.onDestroy();
    }
}
