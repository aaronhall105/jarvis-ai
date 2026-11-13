package com.aaron.jarvisvoice;

import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

/** The real Android org.json escaping/number behavior must match the protected Python verifier. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 35)
public class DevelopmentProductAndroidTest extends DevelopmentProductTest { }
