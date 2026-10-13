using System;
using System.IO;
using System.Reflection;
using System.Text;

// Reads the OAuth client id/secret constants out of Knx.Ets.MyKnx.dll by reflection, so they never
// live in this source tree. They are `const` fields, so GetRawConstantValue reads them straight from
// metadata without running the (obfuscated, dependency-heavy) static constructor.
class ExtractOAuth
{
    static int Main(string[] args)
    {
        if (args.Length < 1) { Console.Error.WriteLine("usage: <dll>"); return 2; }
        Assembly asm = Assembly.LoadFrom(args[0]);
        Type t = asm.GetType("Knx.Ets.MyKnx.OAuth.OAuthDefines");
        if (t == null) { Console.Error.WriteLine("OAuthDefines not found"); return 3; }
        BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static;
        FieldInfo cid = t.GetField("ClientId", flags);
        FieldInfo secret = t.GetField("ClientSecret", flags);
        if (cid == null || secret == null) { Console.Error.WriteLine("client fields not found"); return 4; }
        string cidVal = (string)cid.GetRawConstantValue();
        string secretVal = (string)secret.GetRawConstantValue();
        Console.WriteLine("CID=" + Convert.ToBase64String(Encoding.UTF8.GetBytes(cidVal)));
        Console.WriteLine("SECRET=" + Convert.ToBase64String(Encoding.UTF8.GetBytes(secretVal)));
        return 0;
    }
}
