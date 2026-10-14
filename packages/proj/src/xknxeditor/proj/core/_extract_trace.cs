// Extract the project-trace AES key/IV and marker prefix by reflection.
// Usage: <runtime> _extract_trace.exe <path-to-Knx.Ets.Common.dll> <sentinel>
// Prints three lines to stdout:
//   KEY=<base64 32-byte AES key>
//   IV=<base64 16-byte IV>
//   ENC=<base64 utf8 of Encrypt(sentinel)>   (the caller derives the marker prefix from this)
// Needs a COMPLETE install directory: the encryptor's static ctor drags in log4net, which on CoreCLR
// needs System.Configuration.ConfigurationManager and the sibling assemblies (resolved below).
// Based on the approach of OpenKNXproducer (https://github.com/OpenKNX/OpenKNXproducer).
using System;
using System.IO;
using System.Reflection;
using System.Runtime.Loader;
using System.Security.Cryptography;
using System.Text;

class ExtractTrace
{
    // The trace encryptor. Since 6.4 it is Knx.Ets.Common.Security.ProjectTraceEncryptor; older
    // builds (6.3) obfuscate the class and its method names, so fall back to its shape: a class
    // holding a SymmetricAlgorithm field with public string(string) methods.
    static Type FindEncryptor(Assembly asm)
    {
        Type t = asm.GetType("Knx.Ets.Common.Security.ProjectTraceEncryptor");
        if (t != null) return t;
        // GetTypes() throws if any type in the module fails to load its dependencies; keep the
        // types that did load (the encryptor itself pulls in none of them).
        Type[] types;
        try { types = asm.GetTypes(); }
        catch (ReflectionTypeLoadException ex) { types = ex.Types; }
        foreach (Type tt in types)
        {
            if (tt == null || !tt.IsClass || tt.IsAbstract) continue;
            try
            {
                if (StringMethods(tt).Count >= 2 && HasCipherField(tt)) return tt;
            }
            catch { }
        }
        return null;
    }

    static System.Collections.Generic.List<MethodInfo> StringMethods(Type t)
    {
        var found = new System.Collections.Generic.List<MethodInfo>();
        foreach (MethodInfo m in t.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly))
        {
            ParameterInfo[] ps = m.GetParameters();
            if (m.ReturnType == typeof(string) && ps.Length == 1 && ps[0].ParameterType == typeof(string))
                found.Add(m);
        }
        return found;
    }

    static bool HasCipherField(Type t)
    {
        foreach (FieldInfo f in t.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
        {
            if (typeof(SymmetricAlgorithm).IsAssignableFrom(f.FieldType)) return true;
        }
        return false;
    }

    // Encrypt(sentinel) of the encryptor. Without the method name, Encrypt is the method whose
    // output another method turns back into the sentinel.
    static string EncryptSentinel(Type t, object inst, string sentinel)
    {
        MethodInfo named = t.GetMethod("Encrypt", new Type[] { typeof(string) });
        if (named != null) return (string)named.Invoke(inst, new object[] { sentinel });
        var methods = StringMethods(t);
        foreach (MethodInfo enc in methods)
        {
            string output;
            try { output = (string)enc.Invoke(inst, new object[] { sentinel }); }
            catch { continue; }
            if (output == null || output == sentinel) continue;
            foreach (MethodInfo dec in methods)
            {
                if (dec == enc) continue;
                try
                {
                    if ((string)dec.Invoke(inst, new object[] { output }) == sentinel) return output;
                }
                catch { }
            }
        }
        return null;
    }

    static object Instantiate(Type t)
    {
        const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
        foreach (ConstructorInfo ctor in t.GetConstructors(flags))
        {
            ParameterInfo[] ps = ctor.GetParameters();
            try
            {
                if (ps.Length == 0) return ctor.Invoke(null);
                if (ps.Length == 1 && ps[0].ParameterType == typeof(int)) return ctor.Invoke(new object[] { 1 });
            }
            catch { }
        }
        return null;
    }

    static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: _extract_trace.exe <Knx.Ets.Common.dll> <sentinel>");
            return 2;
        }
        string dll = args[0];
        string sentinel = args[1];
        string dir = Path.GetDirectoryName(Path.GetFullPath(dll));
        // CoreCLR does not probe the DLL's own directory for its sibling assemblies (log4net,
        // Autofac, ...). Resolve them from there so the static ctor can run.
        AssemblyLoadContext.Default.Resolving += (ctx, name) =>
        {
            string cand = Path.Combine(dir, name.Name + ".dll");
            return File.Exists(cand) ? ctx.LoadFromAssemblyPath(cand) : null;
        };
        try
        {
            Assembly asm = Assembly.LoadFrom(dll);
            Type t = FindEncryptor(asm);
            if (t == null)
            {
                Console.Error.WriteLine("project-trace encryptor type not found in " + asm.GetName());
                return 3;
            }
            object inst = Instantiate(t);
            if (inst == null)
            {
                Console.Error.WriteLine("could not instantiate the encryptor " + t.FullName);
                return 4;
            }
            // The key/IV materialize lazily on the first Encrypt/Decrypt, so run one first.
            string output = EncryptSentinel(t, inst, sentinel);
            if (output == null)
            {
                Console.Error.WriteLine("no encrypt method found on the encryptor " + t.FullName);
                return 6;
            }

            SymmetricAlgorithm cipher = null;
            foreach (FieldInfo f in t.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
            {
                if (f.GetValue(inst) is SymmetricAlgorithm sa) { cipher = sa; break; }
            }
            if (cipher == null)
            {
                Console.Error.WriteLine("no AES field found on the encryptor");
                return 5;
            }
            Console.WriteLine("KEY=" + Convert.ToBase64String(cipher.Key));
            Console.WriteLine("IV=" + Convert.ToBase64String(cipher.IV));
            Console.WriteLine("ENC=" + Convert.ToBase64String(Encoding.UTF8.GetBytes(output)));
            Console.Out.Flush();
            // Exit hard: log4net registers a ProcessExit handler that throws on this runtime.
            Environment.Exit(0);
            return 0;
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("ERR: " + e);
            return 1;
        }
    }
}
