import React, { useState, useEffect } from 'react';
import { Mail, Lock, User, UserPlus, LogIn, AlertCircle, Loader2, Moon, Sun, CheckCircle, AtSign } from 'lucide-react';
import { useAuth } from './AuthContext';

export default function LoginSignup() {
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [username, setUsername] = useState('');
  const [fullName, setFullName] = useState('');
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [darkMode, setDarkMode] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');

  const { login, register } = useAuth();

  // Set initial dark mode based on system preference
  useEffect(() => {
    const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    setDarkMode(isDark);
  }, []);

  // Client-side validation
  const validateForm = (): boolean => {
    const errors: Record<string, string> = {};

    // Email validation
    if (!email) {
      errors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      errors.email = 'Invalid email format';
    }

    if (!isLogin) {
      // Username validation (signup only)
      if (!username) {
        errors.username = 'Username is required';
      } else if (username.length < 3) {
        errors.username = 'Username must be at least 3 characters';
      } else if (username.length > 50) {
        errors.username = 'Username must be less than 50 characters';
      } else if (!/^[a-zA-Z0-9_-]+$/.test(username)) {
        errors.username = 'Username can only contain letters, numbers, underscores, and hyphens';
      }
    }

    // Password validation
    if (!password) {
      errors.password = 'Password is required';
    } else if (password.length < 8) {
      errors.password = 'Password must be at least 8 characters';
    } else if (!isLogin && !/\d/.test(password)) {
      errors.password = 'Password must contain at least one digit';
    } else if (!isLogin && !/[A-Z]/.test(password)) {
      errors.password = 'Password must contain at least one uppercase letter';
    }

    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccessMessage('');
    setFieldErrors({});

    // Client-side validation
    if (!validateForm()) {
      return;
    }

    setIsLoading(true);

    try {
      if (isLogin) {
        await login(email, password);
        setSuccessMessage('Login successful! Redirecting...');
      } else {
        await register(
          email,
          password,
          username,
          fullName.trim() || undefined
        );
        setSuccessMessage('Account created! Logging you in...');
      }
    } catch (err: any) {
      console.error('Auth error:', err);
      setError(err.message || 'An error occurred');
    } finally {
      setIsLoading(false);
    }
  };

  const toggleMode = () => {
    setIsLogin(!isLogin);
    setError('');
    setSuccessMessage('');
    setFieldErrors({});
    setEmail('');
    setPassword('');
    setUsername('');
    setFullName('');
  };

  const handleFieldChange = (field: string, value: string) => {
    // Clear field error when user starts typing
    if (fieldErrors[field]) {
      setFieldErrors(prev => {
        const newErrors = { ...prev };
        delete newErrors[field];
        return newErrors;
      });
    }
    setError('');

    // Update field value
    switch(field) {
      case 'email': setEmail(value); break;
      case 'username': setUsername(value); break;
      case 'password': setPassword(value); break;
      case 'fullName': setFullName(value); break;
    }
  };

  return (
    <div className={`min-h-screen transition-all duration-500 ${
      darkMode 
        ? 'bg-gradient-to-br from-slate-950 via-red-950 to-slate-900' 
        : 'bg-gradient-to-br from-white via-red-50 to-white'
    } flex items-center justify-center p-4 relative overflow-hidden`}>
      {/* Animated background elements */}
      <div className={`absolute inset-0 overflow-hidden pointer-events-none ${darkMode ? 'opacity-30' : 'opacity-20'}`}>
        <div className="absolute top-0 left-1/4 w-96 h-96 bg-red-500 rounded-full blur-3xl opacity-30 animate-pulse" />
        <div className="absolute bottom-0 right-1/4 w-96 h-96 bg-red-600 rounded-full blur-3xl opacity-20 animate-pulse animation-delay-2000" />
        <div className="absolute top-1/2 right-0 w-96 h-96 bg-red-700 rounded-full blur-3xl opacity-25 animate-pulse animation-delay-4000" />
      </div>

      {/* Theme Toggle Button - Bottom Right */}
      <button
        onClick={() => setDarkMode(!darkMode)}
        className={`fixed bottom-8 right-8 w-14 h-14 rounded-full shadow-lg transition-all duration-300 flex items-center justify-center z-50 hover:scale-110 ${
          darkMode
            ? 'bg-gradient-to-br from-yellow-400 to-orange-500 text-slate-900'
            : 'bg-gradient-to-br from-slate-700 to-slate-900 text-yellow-300'
        }`}
      >
        {darkMode ? <Sun className="w-6 h-6" /> : <Moon className="w-6 h-6" />}
      </button>

      <div className="w-full max-w-md relative z-10">
        {/* Header */}
        <div className="text-center mb-8">
          <div className={`inline-flex items-center justify-center w-16 h-16 rounded-full mb-4 bg-gradient-to-br from-red-500 to-red-700 shadow-lg transform transition-transform hover:scale-110`}>
            <span className="text-2xl font-bold text-white">PA</span>
          </div>
          <h1 className={`text-4xl font-bold mb-2 ${
            darkMode ? 'text-transparent bg-clip-text bg-gradient-to-r from-red-400 to-orange-400' : 'text-transparent bg-clip-text bg-gradient-to-r from-red-600 to-red-700'
          }`}>
            Pipeline Autopsy
          </h1>
          <p className={`text-sm mb-3 ${
            darkMode ? 'text-gray-300' : 'text-gray-600'
          }`}>
            {isLogin 
              ? 'Investigate data pipeline failures with AI-powered root cause analysis' 
              : 'Create an account to begin investigating pipeline issues'}
          </p>
          <p className={`text-xs ${
            darkMode ? 'text-gray-400' : 'text-gray-500'
          }`}>
            {isLogin ? 'powered by AI & LLM' : 'powered by OpenMetadata & AI'}
          </p>
        </div>

        {/* Form Card */}
        <div className={`backdrop-blur-xl border transition-all duration-300 rounded-3xl shadow-2xl p-8 ${
          darkMode
            ? 'bg-slate-800/40 border-red-500/20 hover:border-red-500/40'
            : 'bg-white/80 border-red-200/50 hover:border-red-400/50'
        }`}>
          {/* Success Message */}
          {successMessage && (
            <div className={`mb-6 p-4 rounded-xl flex items-start gap-3 ${
              darkMode
                ? 'bg-green-900/30 border border-green-700/50'
                : 'bg-green-50 border border-green-300'
            }`}>
              <CheckCircle className={`w-5 h-5 flex-shrink-0 mt-0.5 ${darkMode ? 'text-green-400' : 'text-green-600'}`} />
              <p className={`text-sm ${darkMode ? 'text-green-300' : 'text-green-800'}`}>{successMessage}</p>
            </div>
          )}

          {/* Error Message */}
          {error && (
            <div className={`mb-6 p-4 rounded-xl flex items-start gap-3 animate-shake ${
              darkMode
                ? 'bg-red-900/30 border border-red-700/50'
                : 'bg-red-50 border border-red-300'
            }`}>
              <AlertCircle className={`w-5 h-5 flex-shrink-0 mt-0.5 ${darkMode ? 'text-red-400' : 'text-red-600'}`} />
              <p className={`text-sm ${darkMode ? 'text-red-300' : 'text-red-800'}`}>{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Email Field */}
            <div>
              <label className={`block text-sm font-medium mb-2 ${
                darkMode ? 'text-gray-300' : 'text-gray-700'
              }`}>
                Email Address
              </label>
              <div className="relative group">
                <Mail className={`absolute left-4 top-1/2 transform -translate-y-1/2 w-5 h-5 transition-colors ${
                  darkMode ? 'text-gray-400 group-focus-within:text-red-400' : 'text-gray-400 group-focus-within:text-red-500'
                }`} />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => handleFieldChange('email', e.target.value)}
                  placeholder="you@example.com"
                  required
                  disabled={isLoading}
                  className={`w-full pl-12 pr-4 py-3 rounded-xl border-2 transition-all focus:outline-none focus:scale-105 disabled:opacity-50 disabled:cursor-not-allowed ${
                    fieldErrors.email 
                      ? 'border-red-500' 
                      : darkMode
                      ? 'bg-slate-700/50 border-slate-600 focus:border-red-500 text-white placeholder-gray-400'
                      : 'bg-white border-gray-300 focus:border-red-500 text-black placeholder-gray-400'
                  }`}
                />
              </div>
              {fieldErrors.email && (
                <p className={`mt-1 text-sm ${darkMode ? 'text-red-400' : 'text-red-600'}`}>
                  {fieldErrors.email}
                </p>
              )}
            </div>

            {/* Full Name Field (Sign Up Only) */}
            {!isLogin && (
              <div>
                <label className={`block text-sm font-medium mb-2 ${
                  darkMode ? 'text-gray-300' : 'text-gray-700'
                }`}>
                  Full Name <span className="text-xs text-gray-500">(Optional)</span>
                </label>
                <div className="relative group">
                  <User className={`absolute left-4 top-1/2 transform -translate-y-1/2 w-5 h-5 transition-colors ${
                    darkMode ? 'text-gray-400 group-focus-within:text-red-400' : 'text-gray-400 group-focus-within:text-red-500'
                  }`} />
                  <input
                    type="text"
                    value={fullName}
                    onChange={(e) => handleFieldChange('fullName', e.target.value)}
                    placeholder="John Doe"
                    disabled={isLoading}
                    className={`w-full pl-12 pr-4 py-3 rounded-xl border-2 transition-all focus:outline-none focus:scale-105 disabled:opacity-50 disabled:cursor-not-allowed ${
                      darkMode
                        ? 'bg-slate-700/50 border-slate-600 focus:border-red-500 text-white placeholder-gray-400'
                        : 'bg-white border-gray-300 focus:border-red-500 text-black placeholder-gray-400'
                    }`}
                  />
                </div>
              </div>
            )}

            {/* Username Field (Sign Up Only) */}
            {!isLogin && (
              <div>
                <label className={`block text-sm font-medium mb-2 ${
                  darkMode ? 'text-gray-300' : 'text-gray-700'
                }`}>
                  Username
                </label>
                <div className="relative group">
                  <AtSign className={`absolute left-4 top-1/2 transform -translate-y-1/2 w-5 h-5 transition-colors ${
                    darkMode ? 'text-gray-400 group-focus-within:text-red-400' : 'text-gray-400 group-focus-within:text-red-500'
                  }`} />
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => handleFieldChange('username', e.target.value)}
                    placeholder="john_doe"
                    disabled={isLoading}
                    className={`w-full pl-12 pr-4 py-3 rounded-xl border-2 transition-all focus:outline-none focus:scale-105 disabled:opacity-50 disabled:cursor-not-allowed ${
                      fieldErrors.username
                        ? 'border-red-500'
                        : darkMode
                        ? 'bg-slate-700/50 border-slate-600 focus:border-red-500 text-white placeholder-gray-400'
                        : 'bg-white border-gray-300 focus:border-red-500 text-black placeholder-gray-400'
                    }`}
                  />
                </div>
                {fieldErrors.username && (
                  <p className={`mt-1 text-sm ${darkMode ? 'text-red-400' : 'text-red-600'}`}>
                    {fieldErrors.username}
                  </p>
                )}
                <p className={`mt-1 text-xs ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
                  Letters, numbers, underscores, and hyphens only
                </p>
              </div>
            )}

            {/* OpenMetadata Integration Info (Sign Up Only) */}
            {!isLogin && (
              <div className={`p-3 rounded-lg border ${darkMode ? 'bg-blue-900/20 border-blue-700/30' : 'bg-blue-50 border-blue-200'}`}>
                <p className={`text-xs ${darkMode ? 'text-blue-300' : 'text-blue-800'}`}>
                  💡 After signup, you'll be able to connect your OpenMetadata catalog and GitHub repositories to start investigating pipeline issues.
                </p>
              </div>
            )}

            {/* Password Field */}
            <div>
              <label className={`block text-sm font-medium mb-2 ${
                darkMode ? 'text-gray-300' : 'text-gray-700'
              }`}>
                Password
              </label>
              <div className="relative group">
                <Lock className={`absolute left-4 top-1/2 transform -translate-y-1/2 w-5 h-5 transition-colors ${
                  darkMode ? 'text-gray-400 group-focus-within:text-red-400' : 'text-gray-400 group-focus-within:text-red-500'
                }`} />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => handleFieldChange('password', e.target.value)}
                  placeholder="••••••••"
                  required
                  minLength={8}
                  disabled={isLoading}
                  className={`w-full pl-12 pr-4 py-3 rounded-xl border-2 transition-all focus:outline-none focus:scale-105 disabled:opacity-50 disabled:cursor-not-allowed ${
                    fieldErrors.password 
                      ? 'border-red-500' 
                      : darkMode
                      ? 'bg-slate-700/50 border-slate-600 focus:border-red-500 text-white placeholder-gray-400'
                      : 'bg-white border-gray-300 focus:border-red-500 text-black placeholder-gray-400'
                  }`}
                />
              </div>
              {fieldErrors.password && (
                <p className={`mt-1 text-sm ${darkMode ? 'text-red-400' : 'text-red-600'}`}>
                  {fieldErrors.password}
                </p>
              )}
              {!isLogin && (
                <p className={`mt-1 text-xs ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
                  Min 8 characters, at least one uppercase letter and one digit
                </p>
              )}
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={isLoading}
              className="w-full mt-8 bg-gradient-to-r from-red-500 to-red-700 hover:from-red-600 hover:to-red-800 disabled:from-gray-400 disabled:to-gray-500 text-white py-3 rounded-xl font-bold transition-all duration-300 flex items-center justify-center gap-2 shadow-lg hover:shadow-xl transform hover:scale-105 active:scale-95 disabled:cursor-not-allowed disabled:transform-none"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  {isLogin ? 'Signing in...' : 'Creating account...'}
                </>
              ) : (
                <>
                  {isLogin ? <LogIn className="w-5 h-5" /> : <UserPlus className="w-5 h-5" />}
                  {isLogin ? 'Sign In' : 'Sign Up'}
                </>
              )}
            </button>
          </form>

          {/* Toggle Mode */}
          <div className="mt-6 text-center">
            <p className={`${darkMode ? 'text-gray-300' : 'text-gray-600'}`}>
              {isLogin ? 'New to Pipeline Autopsy? ' : 'Already using Pipeline Autopsy? '}
              <button
                onClick={toggleMode}
                disabled={isLoading}
                className="bg-gradient-to-r from-red-500 to-red-700 bg-clip-text text-transparent hover:from-red-600 hover:to-red-800 font-bold transition-all duration-300 disabled:opacity-50"
              >
                {isLogin ? 'Create account' : 'Sign in'}
              </button>
            </p>
          </div>
        </div>

        {/* Footer */}
        <p className={`text-center text-xs mt-6 ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
          🔍 AI-powered pipeline debugging for data professionals
        </p>
      </div>
    </div>
  );
}