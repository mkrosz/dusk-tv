package io.dusktv.home;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Dusk TV on Android TV and Google TV: the same home screen as on Samsung and LG (web/, bundled in assets/ with
 * index.html carrying the CSS and scripts), in a full-screen WebView. The page reaches the TV through Bridge
 * ("DuskAndroid" in JavaScript); the apps' icons come from https://dusk.icon/<package>, answered here.
 */
public class MainActivity extends Activity {

    static final String ICON_HOST = "https://dusk.icon/";

    private WebView web;
    private Bridge bridge;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        bridge = new Bridge(this);
        web = new WebView(this);
        web.setBackgroundColor(Color.BLACK);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);   // favorites, the chosen photos and the caches live in localStorage
        s.setAllowFileAccess(true);
        // The page comes from file://: the photo, weather and Bing lists are fetched from there, as on the TVs.
        s.setAllowUniversalAccessFromFileURLs(true);
        // Unsplash's photos load over http, like on the 2021 Samsungs (see home.js).
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setMediaPlaybackRequiresUserGesture(false);
        // The page is drawn for 1920 x 1080 (index.html's viewport) and home.js scales it to the screen. Without these,
        // WebView lays it out at the screen's width in dp (960 on a 1080p TV) and everything shows twice as big.
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        web.addJavascriptInterface(bridge, "DuskAndroid");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (url.startsWith(ICON_HOST)) return bridge.iconResponse(url.substring(ICON_HOST.length()));
                return null;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return true;   // the home screen never navigates away
            }
        });
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);
        setContentView(web);
        web.requestFocus(View.FOCUS_DOWN);
        web.loadUrl("file:///android_asset/index.html");
    }

    /**
     * The remote's Back goes to the page (as key code 4, TV.keys.back in tv.js), which closes a menu, goes back to
     * the first tab or, there, calls Bridge.back(). The arrows and OK reach the page by themselves (37-40 and 13).
     */
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        int code = event.getKeyCode(), web = -1;
        if (code == KeyEvent.KEYCODE_BACK) web = 4;
        // The remote's OK (DPAD_CENTER): WebView doesn't hand it to the page as Enter, so it goes as 13 from here.
        // Held, it repeats keydown; home.js counts a hold from the first one (Favoritar / Abrir menu).
        else if (code == KeyEvent.KEYCODE_DPAD_CENTER || code == KeyEvent.KEYCODE_BUTTON_A) web = 13;
        if (web >= 0) {
            if (event.getAction() == KeyEvent.ACTION_DOWN) sendKey("keydown", web);
            else if (event.getAction() == KeyEvent.ACTION_UP) sendKey("keyup", web);
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    private void sendKey(String type, int code) {
        web.evaluateJavascript("(function(){var e=document.createEvent('Event');e.initEvent('" + type +
                "',true,true);e.keyCode=" + code + ";e.which=" + code + ";document.dispatchEvent(e);})()", null);
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        // Back from an app: the page refreshes the clock, the network and the apps (home.js, on visibilitychange).
        web.evaluateJavascript("document.dispatchEvent(new Event('visibilitychange'))", null);
    }

    @Override
    protected void onPause() {
        web.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }

    /** Called by Bridge.back() on the first screen. As the TV's home it stays; as an app it steps aside. */
    void leave() {
        if (!bridge.isDefaultHome()) moveTaskToBack(true);
    }
}
