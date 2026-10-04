package com.mistycloud.accounthelper;

import org.json.JSONArray;
import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** One user-initiated registration, using mail.tm for the verification email. */
final class RegistrationService {
    private static final String MAIL = "https://api.mail.tm";
    private static final String MISTY = "https://mistycapsule.xyz";
    private static final Pattern CODE = Pattern.compile("(?<!\\d)(\\d{6})(?!\\d)");
    private static final String CHARS = "abcdefghijklmnopqrstuvwxyz0123456789";
    private final SecureRandom random = new SecureRandom();

    static final class Mailbox {
        final String email;
        final String password;

        Mailbox(String email, String password) {
            this.email = email;
            this.password = password;
        }
    }

    static final class Failure extends Exception {
        Failure(String message) { super(message); }
    }

    interface Progress { void update(String message); }

    Mailbox createMailbox() throws Failure {
        try {
            JSONArray domains = request("GET", MAIL + "/domains", null, null, null)
                    .optJSONArray("hydra:member");
            String domain = "";
            if (domains != null) {
                for (int i = 0; i < domains.length(); i++) {
                    JSONObject row = domains.optJSONObject(i);
                    if (row != null && row.optBoolean("isActive", true) && !row.optBoolean("isPrivate", false)) {
                        domain = row.optString("domain", "");
                        if (!domain.isEmpty()) break;
                    }
                }
            }
            if (domain.isEmpty()) throw new Failure("临时邮箱服务暂时没有可用域名");
            String email = randomPart(12) + "@" + domain;
            String password = "Misty" + randomPart(18) + "!";
            JSONObject payload = new JSONObject();
            payload.put("address", email);
            payload.put("password", password);
            JSONObject created = request("POST", MAIL + "/accounts", payload.toString(), "application/json", null);
            if (created.optString("id", "").isEmpty()) throw new Failure("临时邮箱创建失败");
            return new Mailbox(created.optString("address", email), password);
        } catch (Failure e) {
            throw e;
        } catch (Exception e) {
            throw new Failure("临时邮箱服务暂时不可用");
        }
    }

    boolean canLogin(Mailbox mailbox) throws Failure {
        try {
            JSONObject response = request("POST", MISTY + "/app/auth/login",
                    form("email", mailbox.email) + "&" + form("passwd", mailbox.password),
                    "application/x-www-form-urlencoded", null);
            return response.optInt("ret", 0) == 1
                    && response.optJSONObject("user") != null
                    && response.optJSONObject("auth") != null;
        } catch (Failure e) {
            throw e;
        } catch (Exception e) {
            throw new Failure("暂时无法检查账号是否已注册，请稍后重试。");
        }
    }

    void register(Mailbox mailbox, Progress progress) throws Failure {
        try {
            String token = token(mailbox);
            Set<String> oldIds = messageIds(token);
            progress.update("正在发送注册验证码…");
            JSONObject sent = request("POST", MISTY + "/app/auth/send",
                    form("email", mailbox.email), "application/x-www-form-urlencoded", null);
            if (sent.optInt("ret", 0) != 1) {
                throw new Failure("验证码发送失败：" + reason(sent));
            }
            progress.update("验证码已发送，正在等待临时邮箱（最多两分钟）…");
            String code = waitForCode(token, oldIds);
            if (code.isEmpty()) throw new Failure("两分钟内未收到注册验证码，可以稍后重试");
            progress.update("已收到验证码，正在完成注册…");
            String body = form("email", mailbox.email)
                    + "&" + form("emailcode", code)
                    + "&" + form("passwd", mailbox.password)
                    + "&" + form("repasswd", mailbox.password)
                    + "&" + form("name", mailbox.email)
                    + "&" + form("code", "0")
                    + "&" + form("signupDevice", "ANDROID")
                    + "&" + form("version", "1.0.0")
                    + "&" + form("channel", "web")
                    + "&" + form("app", "misty");
            JSONObject registered = request("POST", MISTY + "/app/auth/register",
                    body, "application/x-www-form-urlencoded", null);
            if (registered.optInt("ret", 0) != 1) {
                if (canLogin(mailbox)) return;
                throw new Failure("Misty 注册失败：" + reason(registered));
            }
        } catch (Failure e) {
            throw e;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new Failure("注册被中断；下次可以继续尝试");
        } catch (Exception e) {
            throw new Failure("注册暂时失败，请点“继续上次注册”重试。");
        }
    }

    private static String reason(JSONObject response) {
        String message = response.optString("msg", "服务未说明原因").trim();
        return message.length() > 100 ? message.substring(0, 100) : message;
    }

    private String token(Mailbox mailbox) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("address", mailbox.email);
        payload.put("password", mailbox.password);
        String token = request("POST", MAIL + "/token", payload.toString(), "application/json", null)
                .optString("token", "");
        if (token.isEmpty()) throw new Failure("临时邮箱登录失败");
        return token;
    }

    private Set<String> messageIds(String token) throws Exception {
        JSONObject response = request("GET", MAIL + "/messages", null, null, token);
        JSONArray rows = response.optJSONArray("hydra:member");
        Set<String> ids = new HashSet<>();
        if (rows != null) for (int i = 0; i < rows.length(); i++) {
            JSONObject item = rows.optJSONObject(i);
            if (item != null) ids.add(item.optString("id", ""));
        }
        return ids;
    }

    private String waitForCode(String token, Set<String> oldIds) throws Exception {
        long deadline = System.currentTimeMillis() + 120000L;
        while (System.currentTimeMillis() < deadline) {
            JSONObject response = request("GET", MAIL + "/messages", null, null, token);
            JSONArray rows = response.optJSONArray("hydra:member");
            if (rows != null) for (int i = 0; i < rows.length(); i++) {
                JSONObject item = rows.optJSONObject(i);
                if (item == null) continue;
                String id = item.optString("id", "");
                if (oldIds.contains(id) || !id.matches("[A-Za-z0-9-]{1,120}")) continue;
                JSONObject detail = request("GET", MAIL + "/messages/" + id, null, null, token);
                String message = detail.optString("intro", "") + "\n" + detail.optString("subject", "");
                Object text = detail.opt("text");
                if (text instanceof JSONArray) {
                    JSONArray parts = (JSONArray) text;
                    for (int j = 0; j < parts.length(); j++) message += "\n" + parts.optString(j, "");
                } else if (text != null) {
                    message += "\n" + String.valueOf(text);
                }
                Matcher match = CODE.matcher(message);
                if (match.find()) return match.group(1);
            }
            Thread.sleep(4000L);
        }
        return "";
    }

    private String randomPart(int length) {
        StringBuilder out = new StringBuilder(length);
        for (int i = 0; i < length; i++) out.append(CHARS.charAt(random.nextInt(CHARS.length())));
        return out.toString();
    }

    private static String form(String name, String value) throws Exception {
        return URLEncoder.encode(name, "UTF-8") + "=" + URLEncoder.encode(value, "UTF-8");
    }

    private static JSONObject request(String method, String address, String body, String type, String token) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        try {
            connection.setRequestMethod(method);
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(15000);
            connection.setUseCaches(false);
            connection.setInstanceFollowRedirects(false);
            // mail.tm returns a Hydra object for JSON-LD but may return a bare array for JSON.
            connection.setRequestProperty("Accept", address.startsWith(MAIL)
                    ? "application/ld+json" : "application/json");
            connection.setRequestProperty("User-Agent", address.startsWith(MISTY)
                    ? "ClashForWindows/0.20.39" : "MistyAccountHelper/0.1");
            if (token != null) connection.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", type + "; charset=UTF-8");
                connection.setFixedLengthStreamingMode(bytes.length);
                try (OutputStream stream = connection.getOutputStream()) { stream.write(bytes); }
            }
            int code = connection.getResponseCode();
            if (code == 429) throw new Failure("服务请求过于频繁，请稍后再试");
            if (code < 200 || code >= 300) throw new Failure("服务暂时无法响应（HTTP " + code + "）");
            try (InputStream stream = connection.getInputStream()) {
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                byte[] buffer = new byte[4096];
                int count;
                while ((count = stream.read(buffer)) != -1) {
                    bytes.write(buffer, 0, count);
                    if (bytes.size() > 1024 * 1024) throw new Failure("服务返回的数据过大");
                }
                Object decoded = new JSONTokener(new String(bytes.toByteArray(), StandardCharsets.UTF_8)).nextValue();
                if (decoded instanceof JSONObject) return (JSONObject) decoded;
                if (decoded instanceof JSONArray) return new JSONObject().put("hydra:member", decoded);
                throw new Failure("服务返回的数据格式无法识别。");
            }
        } finally { connection.disconnect(); }
    }
}
