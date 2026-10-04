package com.mistycloud.accounthelper;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Keep only the latest completed account and the registration in progress. */
final class RegistrationStore {
    private static final String ALIAS = "misty_quick_register_aes_v1";
    private static final String PREF = "registration_only_v1";
    private static final String FIELD = "encrypted";
    private final SharedPreferences preferences;

    static final class State {
        String lastEmail = "";
        String lastPassword = "";
        long registeredAtMs = 0L;
        String pendingEmail = "";
        String pendingPassword = "";

        State copy() {
            State result = new State();
            result.lastEmail = lastEmail;
            result.lastPassword = lastPassword;
            result.registeredAtMs = registeredAtMs;
            result.pendingEmail = pendingEmail;
            result.pendingPassword = pendingPassword;
            return result;
        }
    }

    RegistrationStore(Context context) {
        preferences = context.getSharedPreferences(PREF, Context.MODE_PRIVATE);
    }

    State load() throws Exception {
        String blob = preferences.getString(FIELD, null);
        State state = new State();
        if (blob == null) return state;
        byte[] packed = Base64.decode(blob, Base64.NO_WRAP);
        if (packed.length < 29) throw new IllegalStateException("加密数据不完整");
        byte[] iv = new byte[12];
        System.arraycopy(packed, 0, iv, 0, iv.length);
        byte[] encrypted = new byte[packed.length - iv.length];
        System.arraycopy(packed, iv.length, encrypted, 0, encrypted.length);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
        JSONObject data = new JSONObject(new String(cipher.doFinal(encrypted), StandardCharsets.UTF_8));
        state.lastEmail = data.optString("lastEmail", "");
        state.lastPassword = data.optString("lastPassword", "");
        state.registeredAtMs = data.optLong("registeredAtMs", 0L);
        state.pendingEmail = data.optString("pendingEmail", "");
        state.pendingPassword = data.optString("pendingPassword", "");
        return state;
    }

    void save(State state) throws Exception {
        JSONObject data = new JSONObject();
        data.put("lastEmail", state.lastEmail);
        data.put("lastPassword", state.lastPassword);
        data.put("registeredAtMs", state.registeredAtMs);
        data.put("pendingEmail", state.pendingEmail);
        data.put("pendingPassword", state.pendingPassword);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] encrypted = cipher.doFinal(data.toString().getBytes(StandardCharsets.UTF_8));
        byte[] iv = cipher.getIV();
        if (iv.length != 12) throw new IllegalStateException("不支持的加密参数");
        byte[] packed = new byte[iv.length + encrypted.length];
        System.arraycopy(iv, 0, packed, 0, iv.length);
        System.arraycopy(encrypted, 0, packed, iv.length, encrypted.length);
        if (!preferences.edit().putString(FIELD, Base64.encodeToString(packed, Base64.NO_WRAP)).commit()) {
            throw new IllegalStateException("保存账号失败");
        }
    }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(ALIAS)) return (SecretKey) store.getKey(ALIAS, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return generator.generateKey();
    }
}
